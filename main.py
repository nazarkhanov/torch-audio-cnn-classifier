import os
import yaml
import pandas as pd
import torch as tf
import torch.nn as nn
import torchaudio as ta
from tqdm import tqdm


CONFIG = None
DEVICE = None
TRANSFORM = None


class Config:
    @staticmethod
    def load(path):
        with open(path, 'r') as stream:
            return yaml.safe_load(stream)


class Device:
    @staticmethod
    def choose():
        global CONFIG
        CONFIG_DEVICE = CONFIG['runtime'].get('device', None)

        if CONFIG_DEVICE != 'auto':
            return CONFIG_DEVICE

        return 'cuda' if tf.cuda.is_available() else 'cpu'


class Dataset(tf.utils.data.Dataset):
    @staticmethod
    def read_annotations():
        global CONFIG
        CONFIG_TRAIN = CONFIG['dataset']['annotations']['train_csv']
        CONFIG_TEST = CONFIG['dataset']['annotations']['test_csv']

        train_annotations = pd.read_csv(CONFIG_TRAIN)
        test_annotations = pd.read_csv(CONFIG_TEST)

        return train_annotations, test_annotations

    @staticmethod
    def load_batches(annotations):
        global CONFIG
        CONFIG_KWARGS = CONFIG['runtime']['loader']

        train_annotations, test_annotations = annotations

        train_dataset = Dataset(train_annotations)
        train_batches = tf.utils.data.DataLoader(train_dataset, **CONFIG_KWARGS)

        test_dataset = Dataset(train_annotations)
        test_batches = tf.utils.data.DataLoader(test_dataset, **CONFIG_KWARGS)

        return train_batches, test_batches

    @staticmethod
    def count_classes(annotations):
        global CONFIG
        CONFIG_TARGET_COL = CONFIG['dataset']['annotations']['target']

        train_annotations, test_annotations = annotations
        df = pd.concat([train_annotations, test_annotations]).nunique()

        return df[CONFIG_TARGET_COL]

    def __init__(self, annotations):
        self.annotations = annotations

    def __len__(self):
        return len(self.annotations)

    def __getitem__(self, idx):
        global CONFIG, DEVICE
        CONFIG_INPUT_COL = CONFIG['dataset']['annotations']['input']
        CONFIG_TARGET_COL = CONFIG['dataset']['annotations']['target']
        CONFIG_FOLDER_PATH = CONFIG['dataset']['folder_path']

        file_path = self.annotations.loc[idx, CONFIG_INPUT_COL]
        full_path = os.path.join(CONFIG_FOLDER_PATH, file_path)
        target = self.annotations.loc[idx, CONFIG_TARGET_COL]

        signal, sr = ta.load(full_path)
        signal.to(DEVICE)

        signal, sr = Dataset.normalize(signal, sr)
        signal = Dataset.transform(signal, sr)

        return signal, target

    @staticmethod
    def normalize(signal, sr):
        global CONFIG
        CONFIG_SAMPLE_RATE = CONFIG['dataset']['normalize']['sample_rate']
        CONFIG_DURATION = CONFIG['dataset']['normalize']['duration']

        duration = CONFIG_SAMPLE_RATE * CONFIG_DURATION
        signal = nn.functional.pad(signal, (0, duration - signal.size()[1]))
        signal = signal.to(DEVICE)

        return signal, sr

    @staticmethod
    def transform(signal, sr):
        global CONFIG, DEVICE, TRANSFORM
        CONFIG_TRANSFORM = CONFIG['runtime']['transform']['name']
        CONFIG_KWARGS = CONFIG['runtime']['transform']['params'] or {}

        if CONFIG_TRANSFORM == 'mel':
            if (TRANSFORM is None) or (type(TRANSFORM) is not ta.transforms.MelSpectrogram):
                TRANSFORM = ta.transforms.MelSpectrogram(sample_rate=sr, **CONFIG_KWARGS).to(DEVICE)
        elif CONFIG_TRANSFORM == 'mfcc':
            if (TRANSFORM is None) or (type(TRANSFORM) is not ta.transforms.MFCC):
                TRANSFORM = ta.transforms.MFCC(sample_rate=sr, **CONFIG_KWARGS).to(DEVICE)
        else:
            raise ValueError('Transform not found')

        transform = TRANSFORM
        signal = transform(signal)

        return signal


class Model:
    @staticmethod
    def init(num_classes):
        global CONFIG
        CONFIG_MODEL = CONFIG['runtime']['model']['name']

        if CONFIG_MODEL == 'custom':
            model = Model.Model1(num_classes)
        else:
            raise ValueError('Model not found')

        return model

    @staticmethod
    def loss():
        return nn.CrossEntropyLoss()

    @staticmethod
    def optimizer(model):
        global CONFIG
        CONFIG_LEARNING_RATE = CONFIG['runtime']['learning_rate']
        return tf.optim.Adam(model.parameters(), lr=CONFIG_LEARNING_RATE)

    class Model1(nn.Module):
        def __init__(self, num_classes):
            super().__init__()

            self.conv1 = nn.Sequential(
                nn.Conv2d(
                    in_channels=1,
                    out_channels=16,
                    kernel_size=3,
                    stride=1,
                    padding=2
                ),
                nn.ReLU(),
                nn.MaxPool2d(kernel_size=2)
            )

            self.conv2 = nn.Sequential(
                nn.Conv2d(
                    in_channels=16,
                    out_channels=32,
                    kernel_size=3,
                    stride=1,
                    padding=2
                ),
                nn.ReLU(),
                nn.MaxPool2d(kernel_size=2)
            )

            self.conv3 = nn.Sequential(
                nn.Conv2d(
                    in_channels=32,
                    out_channels=64,
                    kernel_size=3,
                    stride=1,
                    padding=2
                ),
                nn.ReLU(),
                nn.MaxPool2d(kernel_size=2)
            )

            self.conv4 = nn.Sequential(
                nn.Conv2d(
                    in_channels=64,
                    out_channels=128,
                    kernel_size=3,
                    stride=1,
                    padding=2
                ),
                nn.ReLU(),
                nn.MaxPool2d(kernel_size=2)
            )

            self.flatten = nn.Flatten()
            self.linear = nn.Linear(2304, num_classes)
            self.softmax = nn.Softmax(dim=1)

        def forward(self, data):
            x = self.conv1(data)
            x = self.conv2(x)
            x = self.conv3(x)
            x = self.conv4(x)
            x = self.flatten(x)
            logits = self.linear(x)
            predictions = self.softmax(logits)
            return predictions

    class Model2(nn.Module):
        pass

    class Model3(nn.Module):
        pass


class AudioCNNClassifier:
    @staticmethod
    def main():
        global CONFIG, DEVICE
        CONFIG = Config.load('./config.yml')
        DEVICE = Device.choose()

        print(f'Device: {DEVICE.upper()}')

        annotations = Dataset.read_annotations()
        train_batches, test_batches = Dataset.load_batches(annotations)
        num_classes = Dataset.count_classes(annotations)

        model_obj = Model.init(num_classes).to(DEVICE)
        loss_fn = Model.loss()
        optimiser = Model.optimizer(model_obj)

        AudioCNNClassifier.train_all_epochs(model_obj, loss_fn, optimiser, train_batches)

        tf.save(model_obj.state_dict(), 'model/audio.pth')

    @staticmethod
    def train_all_epochs(model_obj, loss_fn, optimiser, train_batches):
        global CONFIG
        CONFIG_EPOCHS = CONFIG['runtime']['epochs']
        progress = tqdm(range(CONFIG_EPOCHS))

        for i in progress:
            loss = AudioCNNClassifier.train_single_epoch(model_obj, loss_fn, optimiser, train_batches)
            progress.set_description(f'Epoch: {i} | Loss: {loss}')

        print('Finished training')

    @staticmethod
    def train_single_epoch(model_obj, loss_fn, optimiser, train_batches):
        global DEVICE

        for x, y in train_batches:
            x, y = x.to(DEVICE), y.to(DEVICE)

            prediction = model_obj(x)
            loss_val = loss_fn(prediction, y)

            optimiser.zero_grad()
            loss_val.backward()
            optimiser.step()

        return loss_val.item()


if __name__ == '__main__':
    AudioCNNClassifier.main()
