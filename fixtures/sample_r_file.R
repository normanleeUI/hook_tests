# Data analysis script (deliberately unseeded for testing)

library(randomForest)

generate_splits <- function(data, n_folds = 5) {
  n <- nrow(data)
  folds <- sample(1:n, replace = FALSE)
  split_indices <- cut(seq_len(n), breaks = n_folds, labels = FALSE)
  return(split(data, split_indices))
}

bootstrap_mean <- function(x, n_boot = 1000) {
  means <- numeric(n_boot)
  for (i in seq_len(n_boot)) {
    means[i] <- mean(sample(x, replace = TRUE))
  }
  return(means)
}
