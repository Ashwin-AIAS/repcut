"""RIFE's IFNet, model version 4.6: the flow network the slow-motion weights fit.

Vendored, not installed: the network is ~100 lines, the packages that ship it
pull in a video framework Repcut does not use, and a pinned copy here is what
makes the weights' digest (``slowmo/weights.py``) mean something - the same
bytes always load into the same graph.

Origin and licence (MIT, compatible with Repcut's AGPL-3.0):

- Architecture: Practical-RIFE, model 4.6, by hzwer (Huang Zhewei et al.,
  "Real-Time Intermediate Flow Estimation for Video Frame Interpolation",
  ECCV 2022). MIT licence.
- This form (flow inputs passed in, no globals): vs-rife 5.7.0 by HolyWu,
  ``IFNet_HDv3_v4_6.py`` and ``warplayer.py``. MIT licence, Copyright (c) 2021
  HolyWu. The full licence text is in ``docs/licenses/rife.md``.

Changes from the source: type annotations, docstrings, the two files merged,
names kept so the published state dict loads key for key. No behaviour change.

The network only ever blends and warps the two real frames it is given
(``img0``, ``img1``): every output pixel is a weighted mix of pixels the camera
captured, sampled along an estimated motion. That is why it sits inside P1's
gray zone and not outside it - and why ``confidence.py`` exists, because a
wrong motion estimate is still a picture of something that did not happen.
"""

# torch is optional for the engine (amendment 003): this module is imported
# only by ``slowmo.rife`` after it has checked that torch is installed.
import torch
import torch.nn.functional as F
from torch import nn


def warp(
    image: torch.Tensor, flow: torch.Tensor, flow_div: torch.Tensor, grid: torch.Tensor
) -> torch.Tensor:
    """Backward-warp ``image`` along ``flow`` (pixels), in float32 whatever the input."""
    dtype = image.dtype
    image = image.to(torch.float)
    flow = flow.to(torch.float)
    flow = torch.cat([flow[:, 0:1] / flow_div[0], flow[:, 1:2] / flow_div[1]], 1)
    sample = (grid + flow).permute(0, 2, 3, 1)
    return F.grid_sample(
        input=image, grid=sample, mode="bilinear", padding_mode="border", align_corners=True
    ).to(dtype)


def conv(
    in_planes: int,
    out_planes: int,
    kernel_size: int = 3,
    stride: int = 1,
    padding: int = 1,
    dilation: int = 1,
) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(
            in_planes,
            out_planes,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            bias=True,
        ),
        nn.LeakyReLU(0.2, True),
    )


class ResConv(nn.Module):
    def __init__(self, c: int, dilation: int = 1) -> None:
        super().__init__()
        self.conv = nn.Conv2d(c, c, 3, 1, dilation, dilation=dilation, groups=1)
        self.beta = nn.Parameter(torch.ones((1, c, 1, 1)), requires_grad=True)
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out: torch.Tensor = self.relu(self.conv(x) * self.beta + x)
        return out


class IFBlock(nn.Module):
    def __init__(self, in_planes: int, c: int = 64) -> None:
        super().__init__()
        self.conv0 = nn.Sequential(conv(in_planes, c // 2, 3, 2, 1), conv(c // 2, c, 3, 2, 1))
        self.convblock = nn.Sequential(*(ResConv(c) for _ in range(8)))
        self.lastconv = nn.Sequential(nn.ConvTranspose2d(c, 4 * 6, 4, 2, 1), nn.PixelShuffle(2))

    def forward(
        self, x: torch.Tensor, flow: torch.Tensor | None = None, scale: float = 1
    ) -> tuple[torch.Tensor, torch.Tensor]:
        x = F.interpolate(x, scale_factor=1.0 / scale, mode="bilinear")
        if flow is not None:
            flow = F.interpolate(flow, scale_factor=1.0 / scale, mode="bilinear") / scale
            x = torch.cat((x, flow), 1)
        feat = self.conv0(x)
        feat = self.convblock(feat)
        tmp = self.lastconv(feat)
        tmp = F.interpolate(tmp, scale_factor=scale, mode="bilinear")
        return tmp[:, :4] * scale, tmp[:, 4:5]


class IFNet(nn.Module):
    """Four coarse-to-fine flow blocks; ``scale`` < 1 estimates flow at lower resolution.

    ``scale`` is the memory lever on a 4 GB card: 0.5 runs the flow blocks at
    half resolution (four times fewer activations) while the final warp still
    samples the full-resolution frames.
    """

    def __init__(self, scale: float = 1, ensemble: bool = False) -> None:
        super().__init__()
        self.block0 = IFBlock(7, c=192)
        self.block1 = IFBlock(8 + 4, c=128)
        self.block2 = IFBlock(8 + 4, c=96)
        self.block3 = IFBlock(8 + 4, c=64)
        self.scale_list = [8 / scale, 4 / scale, 2 / scale, 1 / scale]
        self.ensemble = ensemble

    def forward(
        self,
        img0: torch.Tensor,
        img1: torch.Tensor,
        timestep: torch.Tensor,
        flow_div: torch.Tensor,
        grid: torch.Tensor,
    ) -> torch.Tensor:
        img0 = img0.clamp(0.0, 1.0)
        img1 = img1.clamp(0.0, 1.0)
        warped_img0 = img0
        warped_img1 = img1
        flow: torch.Tensor | None = None
        mask: torch.Tensor | None = None
        blocks = [self.block0, self.block1, self.block2, self.block3]
        for index, block in enumerate(blocks):
            scale = self.scale_list[index]
            if flow is None or mask is None:
                flow, mask = block(torch.cat((img0, img1, timestep), 1), None, scale=scale)
                if self.ensemble:
                    f1, m1 = block(torch.cat((img1, img0, 1 - timestep), 1), None, scale=scale)
                    flow = (flow + torch.cat((f1[:, 2:4], f1[:, :2]), 1)) / 2
                    mask = (mask + (-m1)) / 2
            else:
                f0, m0 = block(
                    torch.cat((warped_img0, warped_img1, timestep, mask), 1), flow, scale=scale
                )
                if self.ensemble:
                    f1, m1 = block(
                        torch.cat((warped_img1, warped_img0, 1 - timestep, -mask), 1),
                        torch.cat((flow[:, 2:4], flow[:, :2]), 1),
                        scale=scale,
                    )
                    f0 = (f0 + torch.cat((f1[:, 2:4], f1[:, :2]), 1)) / 2
                    m0 = (m0 + (-m1)) / 2
                flow = flow + f0
                mask = mask + m0
            warped_img0 = warp(img0, flow[:, :2], flow_div, grid)
            warped_img1 = warp(img1, flow[:, 2:4], flow_div, grid)
        assert mask is not None  # noqa: S101 - four blocks always ran; narrows the type for mypy
        weight = torch.sigmoid(mask)
        merged: torch.Tensor = warped_img0 * weight + warped_img1 * (1 - weight)
        return merged


__all__ = ["IFNet", "warp"]
