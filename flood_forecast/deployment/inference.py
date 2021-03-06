from flood_forecast.time_model import PyTorchForecast
from flood_forecast.evaluator import infer_on_torch_model
from flood_forecast.plot_functions import plot_df_test_with_confidence_interval
from flood_forecast.explain_model_output import deep_explain_model_heatmap, deep_explain_model_summary_plot
from flood_forecast.time_model import scaling_function
# from flood_forecast.preprocessing.buil_dataset import get_data
from flood_forecast.gcp_integration.basic_utils import upload_file
from datetime import datetime
import wandb
import torch


class InferenceMode(object):
    def __init__(self, hours_to_forecast: int, num_prediction_samples: int, model_params, csv_path: str, weight_path,
                 wandb_proj: str = None, torch_script=False):
        """Class to handle inference for models

        :param hours_to_forecast: [description]
        :type hours_to_forecast: int
        :param num_prediction_samples: [description]
        :type num_prediction_samples: int
        :param model_params: [description]
        :type model_params: [type]
        :param csv_path: [description]
        :type csv_path: str
        :param weight_path: [description]
        :type weight_path: [type]
        :param wandb_proj: [description], defaults to None
        :type wandb_proj: str, optional
        """
        self.hours_to_forecast = hours_to_forecast
        self.csv_path = csv_path
        self.model = load_model(model_params.copy(), csv_path, weight_path)
        self.inference_params = model_params["inference_params"]
        if "scaling" in self.inference_params["dataset_params"]:
            s = scaling_function({}, self.inference_params["dataset_params"])["scaling"]
            self.inference_params["dataset_params"]["scaling"] = s
        self.inference_params["hours_to_forecast"] = hours_to_forecast
        self.inference_params["num_prediction_samples"] = num_prediction_samples
        if wandb_proj:
            date = datetime.now()
            wandb.init(name=date.strftime("%H-%M-%D-%Y") + "_prod", project=wandb_proj)
            wandb.config.update(model_params)

    def infer_now(self, some_date: datetime, csv_path=None, save_buck=None, save_name=None, use_torch_script=False):
        """Performs inference at a specified datatime

        :param some_date: The date you want inference to begin on.
        :param csv_path: [description], defaults to None
        :type csv_path: [type], optional
        :param save_buck: [description], defaults to None
        :type save_buck: [type], optional
        :param save_name: The name of the file to save the Pandas data-frame to GCP as, defaults to None
        :type save_name: [type], optional
        :return: Returns a tuple consisting of the Pandas dataframe with predictions + history,
        the prediction tensor, a tensor of the historical values, the forecast start index, and the test
        :rtype: [type]
        """
        forecast_history = self.inference_params["dataset_params"]["forecast_history"]
        self.inference_params["datetime_start"] = some_date
        if csv_path:
            self.inference_params["test_csv_path"] = csv_path
            self.inference_params["dataset_params"]["file_path"] = csv_path
        df, tensor, history, forecast_start, test, samples = infer_on_torch_model(self.model, **self.inference_params)
        if test.scale:
            unscaled = test.inverse_scale(tensor.numpy().reshape(-1, 1))
            df["preds"][forecast_history:] = unscaled.numpy()[:, 0]
        if len(samples) > 1:
            samples[:forecast_history] = 0
        if save_buck:
            df.to_csv("temp3.csv")
            upload_file(save_buck, save_name, "temp3.csv", self.model.gcs_client)
        return df, tensor, history, forecast_start, test, samples

    def make_plots(self, date: datetime, csv_path: str = None, csv_bucket: str = None,
                   save_name=None, wandb_plot_id=None):
        """

        :param date: [description]
        :type date: datetime
        :param csv_path: [description], defaults to None
        :type csv_path: str, optional
        :param csv_bucket: [description], defaults to None
        :type csv_bucket: str, optional
        :param save_name: [description], defaults to None
        :type save_name: [type], optional
        :param wandb_plot_id: [description], defaults to None
        :type wandb_plot_id: [type], optional
        :return: [description]
        :rtype: [type]
        """
        if csv_path is None:
            csv_path = self.csv_path
        df, tensor, history, forecast_start, test, samples = self.infer_now(date, csv_path, csv_bucket, save_name)
        plt = {}
        for sample in samples:
            plt = plot_df_test_with_confidence_interval(df, sample, forecast_start, self.model.params)
            if wandb_plot_id:
                wandb.log({wandb_plot_id: plt})
                deep_explain_model_summary_plot(self.model, test, date)
                deep_explain_model_heatmap(self.model, test, date)
        return tensor, history, test, plt


def convert_to_torch_script(model: PyTorchForecast, save_path: str) -> PyTorchForecast:
    """Function to convert PyTorch model to torch script and save

    :param model: The PyTorchForecast model you wish to convert
    :type model: PyTorchForecast
    :param save_path: File name to save the TorchScript model under.
    :type save_path: str
    :return: Returns the model with an added .script_model attribute
    :rtype: PyTorchForecast
    """
    model.model.eval()
    forecast_history = model.params["dataset_params"]["forecast_history"]
    n_features = model.params["model_params"]["n_time_series"]
    test_input = torch.rand(2, forecast_history, n_features)
    model_script = torch.jit.trace(model.model, test_input)
    test_input1 = torch.rand(4, forecast_history, n_features)
    a = model_script(test_input1)
    b = model.model(test_input1)
    model.script_model = model_script
    assert torch.eq(a, b).all()
    model_script.save(save_path)
    return model


def load_model(model_params_dict, file_path, weight_path: str) -> PyTorchForecast:
    """Function to load a PyTorchForecast model from an existing config file.

    :param model_params_dict: Dictionary of model parameters
    :type model_params_dict: Dict
    :param file_path: [description]
    :type file_path: str
    :param weight_path: [description]
    :type weight_path: str
    :return: [description]
    :rtype: PyTorchForecast
    """
    if weight_path:
        model_params_dict["weight_path"] = weight_path
    model_params_dict["inference_params"]["test_csv_path"] = file_path
    model_params_dict["inference_params"]["dataset_params"]["file_path"] = file_path
    m = PyTorchForecast(model_params_dict["model_name"], file_path, file_path, file_path, model_params_dict)
    return m
