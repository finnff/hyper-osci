#!/usr/bin/env python3
"""Quick shaded previews of the case parts.  Not a renderer, just a sanity view.

    hw/case/.venv/bin/python hw/case/tools/render.py   ->  out/preview.png
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "..", "out")


def read_stl(path):
    with open(path, "rb") as fh:
        fh.read(80)
        n = int(np.frombuffer(fh.read(4), "<u4")[0])
        data = np.frombuffer(fh.read(n * 50), dtype=np.dtype([
            ("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    return data["v"].astype(float), data["n"].astype(float)


def panel(ax, meshes, title, elev, azim, alphas=None):
    allv = np.concatenate([v.reshape(-1, 3) for v, _ in meshes])
    alphas = alphas or [1.0] * len(meshes)
    for (v, nrm), color, alpha in zip(meshes, COLORS, alphas):
        light = np.array([0.4, -0.7, 0.6]); light /= np.linalg.norm(light)
        ln = np.linalg.norm(nrm, axis=1, keepdims=True); ln[ln == 0] = 1
        shade = 0.42 + 0.58 * np.clip(nrm / ln @ light, 0, 1)
        fc = np.array(matplotlib.colors.to_rgb(color))[None, :] * shade[:, None]
        ax.add_collection3d(Poly3DCollection(v, facecolors=np.clip(fc, 0, 1),
                                             edgecolors="none", linewidths=0, alpha=alpha))
    c = (allv.max(0) + allv.min(0)) / 2
    r = (allv.max(0) - allv.min(0)).max() / 2 * 1.02
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off(); ax.set_title(title, fontsize=11, color="#222", pad=0)


COLORS = ["#5b8dd6", "#d68a5b", "#7ac07a"]


def shift(mesh, dx, dy, dz):
    v, n = mesh
    return v + np.array([dx, dy, dz]), n

if __name__ == "__main__":
    base = read_stl(os.path.join(OUT, "base.stl"))
    lid = read_stl(os.path.join(OUT, "lid.stl"))
    brd = os.path.join(OUT, "board.stl")
    board = read_stl(brd) if os.path.exists(brd) else None

    both = [base] + ([board] if board else [])
    fig = plt.figure(figsize=(16, 10), facecolor="white")
    views = [
        ([base], "base — E wall: TP4056 charge port + mic pigtail", 20, -35),
        ([base], "base — S wall: pot shaft, power switch, JST/lead slot", 20, 145),
        ([base], "base — N wall: the two RCA flying-lead slots", 20, 55),
        (both, "base + populated carrier (base ghosted)", 46, -62, [0.16, 1.0]),
        ([lid], "lid — underside: lip, 4 snaps, vents, SW2 + LED holes", -35, -60),
        ([shift(read_stl(os.path.join(OUT, "knob.stl")), -11, 0, 0),
          shift(read_stl(os.path.join(OUT, "plunger.stl")), 11, 0, 0)],
         "RV1 knob (optional) + SW2 plunger", 18, -60),
    ]
    for i, view in enumerate(views, 1):
        meshes, title, el, az = view[:4]
        ax = fig.add_subplot(2, 3, i, projection="3d", facecolor="white")
        panel(ax, meshes, title, el, az, view[4] if len(view) > 4 else None)
    fig.suptitle("HYPEROSCI slave enclosure — generated from carrier.kicad_pcb",
                 fontsize=13, y=0.97)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "preview.png"), dpi=115, facecolor="white")
    print("wrote out/preview.png")
