"""MinimapNet: a light CenterNet that finds champion icons on a 256 px minimap.

Architecture (input ``[B, 3, 256, 256]`` RGB in 0..1, output grids at stride 4 = 64x64)::

    stem   conv3x3 s2 (16)                                   -> stride 2
    stage1 inverted residuals (24)                           -> stride 4
    stage2 inverted residuals (48)                           -> stride 8
    stage3 inverted residuals (96)                           -> stride 16
    FPN    1x1 laterals to 64 ch, nearest upsample + add, depthwise-separable smoothing
    heads  heatmap (1, sigmoid) | cls (3, softmax) | offset (2, sigmoid) | radius (1, relu)

Only simple ONNX operators are used (Conv, BatchNormalization, Relu/Clip, Add, Resize
nearest, Sigmoid, Softmax); BatchNorm and the radius scale fold into the convolutions at
export (``do_constant_folding``).

``forward(x)`` returns the *activated* tensors ``(heatmap, cls, offset, radius)`` in the
order and meaning of the ONNX contract (ARCHITECTURE.md §4.9). Training uses
:meth:`MinimapNet.forward_train`, which returns the heatmap and class *logits* (for the
focal loss / cross-entropy) together with the activated offset and radius.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import torch
import torch.nn.functional as F
from torch import nn

INPUT_SIZE = 256
STRIDE = 4
CLASSES = ("enemy", "ally", "self")
NUM_CLASSES = len(CLASSES)
HEATMAP_PRIOR = 0.1                         # bias init -log((1 - p) / p) = -2.19
RADIUS_SCALE = 1.0 / 16.0                   # radius = relu(raw) * RADIUS_SCALE (folded at export)
RADIUS_INIT = 0.047                         # typical normalized icon radius


class TrainOutputs(NamedTuple):
    """Raw-ish outputs used by the training losses."""

    heatmap_logits: torch.Tensor   # [B, 1, H, W]
    cls_logits: torch.Tensor       # [B, C, H, W]
    offset: torch.Tensor           # [B, 2, H, W] sigmoid, in cells
    radius: torch.Tensor           # [B, 1, H, W] normalized by the input size, >= 0


def conv_bn(cin: int, cout: int, k: int = 3, s: int = 1, groups: int = 1,
            act: bool = True) -> nn.Sequential:
    """Conv (no bias) + BatchNorm (+ ReLU6)."""
    layers: list[nn.Module] = [
        nn.Conv2d(cin, cout, k, s, k // 2, groups=groups, bias=False),
        nn.BatchNorm2d(cout),
    ]
    if act:
        layers.append(nn.ReLU6(inplace=True))
    return nn.Sequential(*layers)


class InvertedResidual(nn.Module):
    """MobileNetV2 block: 1x1 expand -> 3x3 depthwise (stride s) -> 1x1 project (+ skip)."""

    def __init__(self, cin: int, cout: int, stride: int = 1, expand: int = 4) -> None:
        super().__init__()
        hidden = cin * expand
        self.use_skip = stride == 1 and cin == cout
        layers: list[nn.Module] = []
        if expand != 1:
            layers.append(conv_bn(cin, hidden, 1))
        layers.append(conv_bn(hidden, hidden, 3, stride, groups=hidden))
        layers.append(conv_bn(hidden, cout, 1, act=False))
        self.block = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.block(x)
        return x + y if self.use_skip else y


class SepConv(nn.Module):
    """Depthwise 3x3 + pointwise 1x1 (both with BN + ReLU6)."""

    def __init__(self, cin: int, cout: int) -> None:
        super().__init__()
        self.dw = conv_bn(cin, cin, 3, groups=cin)
        self.pw = conv_bn(cin, cout, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.pw(self.dw(x))


class ScaledConv1x1(nn.Conv2d):
    """1x1 conv whose effective weights are ``scale * (weight, bias)``.

    A re-parametrisation that lets the raw parameters live at O(1) while the output is
    small (normalized radius ~0.05). At ONNX export the products are constant-folded, so
    the graph contains a plain ``Conv``.
    """

    def __init__(self, cin: int, cout: int, scale: float) -> None:
        super().__init__(cin, cout, 1, bias=True)
        self.scale = float(scale)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D102
        bias = self.bias * self.scale if self.bias is not None else None
        return F.conv2d(x, self.weight * self.scale, bias)


class Head(nn.Module):
    """Prediction head: depthwise-separable 3x3 (64 -> hidden) then 1x1 output conv."""

    def __init__(self, cin: int, cout: int, hidden: int = 32, out: nn.Conv2d | None = None) -> None:
        super().__init__()
        self.body = SepConv(cin, hidden)
        self.out = out if out is not None else nn.Conv2d(hidden, cout, 1, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.out(self.body(x))


class MinimapNet(nn.Module):
    """Light CenterNet (~0.5 M parameters) for minimap champion icons.

    ``width`` scales every channel count (1.0 -> stages 24/48/96, FPN 64).
    """

    def __init__(self, num_classes: int = NUM_CLASSES, width: float = 1.0,
                 fpn_channels: int = 64, head_channels: int = 32) -> None:
        super().__init__()

        def ch(c: int) -> int:
            return max(8, int(round(c * width / 8.0)) * 8)

        c0, c1, c2, c3 = ch(16), ch(24), ch(48), ch(96)
        fpn = ch(fpn_channels)
        self.num_classes = num_classes
        self.config = {"num_classes": num_classes, "width": width,
                       "fpn_channels": fpn_channels, "head_channels": head_channels}

        self.stem = conv_bn(3, c0, 3, 2)                                   # /2
        self.stage1 = nn.Sequential(InvertedResidual(c0, c1, 2, 4),        # /4
                                    InvertedResidual(c1, c1, 1, 3))
        self.stage2 = nn.Sequential(InvertedResidual(c1, c2, 2, 4),        # /8
                                    InvertedResidual(c2, c2, 1, 4),
                                    InvertedResidual(c2, c2, 1, 4))
        self.stage3 = nn.Sequential(InvertedResidual(c2, c3, 2, 4),        # /16
                                    InvertedResidual(c3, c3, 1, 4),
                                    InvertedResidual(c3, c3, 1, 4),
                                    InvertedResidual(c3, c3, 1, 4))
        self.lat3 = conv_bn(c3, fpn, 1)
        self.lat2 = conv_bn(c2, fpn, 1)
        self.lat1 = conv_bn(c1, fpn, 1)
        self.smooth2 = SepConv(fpn, fpn)
        self.smooth1 = SepConv(fpn, fpn)

        hc = head_channels
        self.head_hm = Head(fpn, 1, hc)
        self.head_cls = Head(fpn, num_classes, hc)
        self.head_off = Head(fpn, 2, hc)
        self.head_rad = Head(fpn, 1, hc, out=ScaledConv1x1(hc, 1, RADIUS_SCALE))
        self._init_weights()

    # ------------------------------------------------------------------ init
    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
        # zero-init the last BN of each residual branch (starts as identity)
        for m in self.modules():
            if isinstance(m, InvertedResidual) and m.use_skip:
                nn.init.zeros_(m.block[-1][1].weight)
        for head in (self.head_hm, self.head_cls, self.head_off, self.head_rad):
            nn.init.normal_(head.out.weight, std=0.01)
        nn.init.constant_(self.head_hm.out.bias, -math.log((1.0 - HEATMAP_PRIOR) / HEATMAP_PRIOR))
        nn.init.zeros_(self.head_off.out.bias)
        nn.init.constant_(self.head_rad.out.bias, RADIUS_INIT / RADIUS_SCALE)

    # ------------------------------------------------------------------ forward
    def features(self, x: torch.Tensor) -> torch.Tensor:
        """Stride-4 fused feature map ``[B, fpn, S/4, S/4]``."""
        c1 = self.stage1(self.stem(x))
        c2 = self.stage2(c1)
        c3 = self.stage3(c2)
        p = self.lat3(c3)
        p = self.smooth2(self.lat2(c2) + F.interpolate(p, scale_factor=2.0, mode="nearest"))
        p = self.smooth1(self.lat1(c1) + F.interpolate(p, scale_factor=2.0, mode="nearest"))
        return p

    def forward_train(self, x: torch.Tensor) -> TrainOutputs:
        """Outputs for the losses (heatmap / class logits, activated offset / radius)."""
        f = self.features(x)
        return TrainOutputs(
            heatmap_logits=self.head_hm(f),
            cls_logits=self.head_cls(f),
            offset=torch.sigmoid(self.head_off(f)),
            radius=F.relu(self.head_rad(f)),
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Activated outputs ``(heatmap, cls, offset, radius)`` (the ONNX contract)."""
        o = self.forward_train(x)
        return (torch.sigmoid(o.heatmap_logits), torch.softmax(o.cls_logits, dim=1),
                o.offset, o.radius)


def count_parameters(model: nn.Module) -> int:
    """Number of trainable parameters."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def build_model(config: dict | None = None) -> MinimapNet:
    """Create a :class:`MinimapNet` from a (checkpoint) config dict."""
    cfg = dict(config or {})
    allowed = {"num_classes", "width", "fpn_channels", "head_channels"}
    return MinimapNet(**{k: v for k, v in cfg.items() if k in allowed})
