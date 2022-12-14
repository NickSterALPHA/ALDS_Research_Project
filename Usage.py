# %% import the required packages
import os
import copy
import torch
import torchvision
import torchprune as tp

# %% initialize the network and wrap it into the NetHandle class
net_name = "resnet20_CIFAR10"
net = tp.util.models.resnet20()
net = tp.util.net.NetHandle(net, net_name)

# %% Setup some stats to track results and retrieve checkpoints
n_idx = 0  # network index 0
keep_ratio = 0.5  # Ratio of parameters to keep
s_idx = 0  # keep ratio's index
r_idx = 0  # repetition index

# %% initialize data loaders with a limited number of points
transform_train = [
    torchvision.transforms.Pad(4),
    torchvision.transforms.RandomCrop(32),
    torchvision.transforms.RandomHorizontalFlip(),
]
transform_static = [
    torchvision.transforms.ToTensor(),
    torchvision.transforms.Normalize(
        (0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)
    ),
]


testset = torchvision.datasets.CIFAR10(
    root="./local",
    train=False,
    download=True,
    transform=tp.util.transforms.SmartCompose(transform_static),
)

trainset = torchvision.datasets.CIFAR10(
    root="./local",
    train=True,
    download=True,
    transform=tp.util.transforms.SmartCompose(
        transform_train + transform_static
    ),
)

size_s = 128
batch_size = 128
testset, set_s = torch.utils.data.random_split(
    testset, [len(testset) - size_s, size_s]
)

loader_s = torch.utils.data.DataLoader(set_s, batch_size=32, shuffle=False)
loader_test = torch.utils.data.DataLoader(
    testset, batch_size=batch_size, shuffle=False
)
loader_train = torch.utils.data.DataLoader(
    trainset, batch_size=batch_size, shuffle=False
)

# %% Setup trainer
# Set up training parameters
train_params = {
    # any loss and corresponding kwargs for __init__ from tp.util.nn_loss
    "loss": "CrossEntropyLoss",
    "lossKwargs": {"reduction": "mean"},
    # exactly two metrics with __init__ kwargs from tp.util.metrics
    "metricsTest": [
        {"type": "TopK", "kwargs": {"topk": 1}},
        {"type": "TopK", "kwargs": {"topk": 5}},
    ],
    # any optimizer from torch.optim with corresponding __init__ kwargs
    "optimizer": "SGD",
    "optimizerKwargs": {
        "lr": 0.1,
        "weight_decay": 1.0e-4,
        "nesterov": False,
        "momentum": 0.9,
    },
    # batch size
    "batchSize": batch_size,
    # desired number of epochs
    "startEpoch": 0,
    "retrainStartEpoch": -1,
    "numEpochs": 5,  # 182
    # any desired combination of lr schedulers from tp.util.lr_scheduler
    "lrSchedulers": [
        {
            "type": "MultiStepLR",
            "stepKwargs": {"milestones": [91, 136]},
            "kwargs": {"gamma": 0.1},
        },
        {"type": "WarmupLR", "stepKwargs": {"warmup_epoch": 5}, "kwargs": {}},
    ],
    # output size of the network
    "outputSize": 10,
    # directory to store checkpoints
    "dir": os.path.realpath("./checkpoints"),
}

# Setup retraining parameters (just copy train-parameters)
retrain_params = copy.deepcopy(train_params)

# Setup trainer
trainer = tp.util.train.NetTrainer(
    train_params=train_params,
    retrain_params=retrain_params,
    train_loader=loader_train,
    test_loader=loader_test,
    valid_loader=loader_s,
    num_gpus=1,
)

# get a loss handle
loss_handle = trainer.get_loss_handle()

# %% Pre-train the network
trainer.train(net, n_idx)

# %% Prune weights on the CPU

print("\n===========================")
print("Pruning weights with SiPP")
net_weight_pruned = tp.SiPPNet(net, loader_s, loss_handle)
net_weight_pruned.compress(keep_ratio=keep_ratio)
print(
    f"The network has {net_weight_pruned.size()} parameters and "
    f"{net_weight_pruned.flops()} FLOPs left."
)
print("===========================")

# %% Prune filters on the GPU
print("\n===========================")
print("Pruning filters with PFP.")
net_filter_pruned = tp.PFPNet(net, loader_s, loss_handle)
net_filter_pruned.cuda()
net_filter_pruned.compress(keep_ratio=keep_ratio)
net_filter_pruned.cpu()
print(
    f"The network has {net_filter_pruned.size()} parameters and "
    f"{net_filter_pruned.flops()} FLOPs left."
)
print("===========================")

# %% Retrain the filter-pruned network now.

# Retrain the filter-pruned network now on the GPU
net_filter_pruned = net_filter_pruned.cuda()
trainer.retrain(net_filter_pruned, n_idx, keep_ratio, s_idx, r_idx)

# %% Test at the end
print("\nTesting on test data set:")
loss, acc1, acc5 = trainer.test(net_filter_pruned)
print(f"Loss: {loss:.4f}, Top-1 Acc: {acc1*100:.2f}%, Top-5: {acc5*100:.2f}%")

# Put back to CPU
net_filter_pruned = net_filter_pruned.cpu()