"""Data generation utilities (deliberately unseeded for testing)."""

import numpy as np
from scipy import stats


def generate_training_data(n_samples: int, n_features: int = 10):
    """Generate synthetic regression data."""
    X = np.random.randn(n_samples, n_features)
    noise = stats.norm.rvs(size=n_samples)
    weights = np.random.uniform(-1, 1, size=n_features)
    y = X @ weights + noise
    return X, y


def random_split(data, ratio: float = 0.8):
    """Randomly split data into train/test sets."""
    n = len(data)
    indices = np.random.permutation(n)
    split_point = int(n * ratio)
    return data[indices[:split_point]], data[indices[split_point:]]
