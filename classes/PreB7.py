# ---------- Torch / ML ----------
import joblib, torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image
import os, time, json, threading, warnings, sys
from concurrent.futures import ThreadPoolExecutor
import cv2
from pathlib import Path
from paths import APP_DIR
import numpy as np
from typing import Dict, Tuple, Optional, List



# ================= EfficientNet-B7 Feature Model ==================
class EffB7_Feature(nn.Module):
    def __init__(self):
        super().__init__()
        m = models.efficientnet_b7(weights=models.EfficientNet_B7_Weights.IMAGENET1K_V1)
        self.backbone = m.features
        self.pool     = nn.AdaptiveAvgPool2d((1, 1))
        self.eval()
        for p in self.parameters():
            p.requires_grad = False
    @torch.no_grad()
    def forward(self, x):
        x = self.backbone(x)
        x = self.pool(x)
        x = torch.flatten(x, 1)  # [B, 2560]
        return x

# ============ B7 + kNN Predictor (batched) ============
class B7KnnPredictor:
    def __init__(self, artifact_path: str, fixed_threshold: float, use_cpu: bool=False, use_compile: bool=True, warmup_iters: int=3):
        self.fixed_threshold = float(fixed_threshold)

        # Device / precision
        self.use_cuda = (not use_cpu) and torch.cuda.is_available()
        self.device   = torch.device("cuda" if self.use_cuda else "cpu")
        self.amp_dtype = torch.bfloat16 if self.use_cuda and torch.cuda.is_bf16_supported() else torch.float16

        # cuDNN/autotune
        torch.backends.cudnn.benchmark = True
        try:
            torch.set_float32_matmul_precision("high")
        except Exception:
            pass

        # Load artifact once
        self.artifact = joblib.load(artifact_path)
        self.img_size = int(self.artifact["img_size"])
        self.knn      = self.artifact["knn"]
        self.mean     = self.artifact["embed_mean"]  # shape (2560,)
        self.std      = self.artifact["embed_std"]   # shape (2560,)

        # Model
        self.model = EffB7_Feature().to(self.device)
        if self.use_cuda:
            self.model = self.model.to(memory_format=torch.channels_last)

        if self.use_cuda and use_compile:
            try:
                self.model = torch.compile(self.model, mode="max-autotune")
            except Exception as e:
                warnings.warn(f"torch.compile failed/unsupported: {e}")

        # Preprocessing (CPU; tensors later moved to GPU)
        self.tf = transforms.Compose([
            transforms.Resize((self.img_size, self.img_size)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225]),
        ])

        # Thread pool for CPU preprocessing
        # self._cpu_pool = ThreadPoolExecutor(max_workers=4)

        # Warmup
        if self.use_cuda and warmup_iters > 0:
            dummy = torch.randn(4, 3, self.img_size, self.img_size, device=self.device).to(memory_format=torch.channels_last)
            with torch.no_grad(), torch.amp.autocast("cuda", dtype=self.amp_dtype, enabled=self.use_cuda):
                _ = self.model(dummy)
            torch.cuda.synchronize()

    def _to_pil(self, bgr: np.ndarray) -> Image.Image:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def _prep_one(self, bgr: np.ndarray) -> torch.Tensor:
        pil = self._to_pil(bgr)
        x = self.tf(pil)  # CPU float32 [3,H,W]
        return x

   

    @torch.no_grad()
    def predict_single_from_bgr(self, bgr_img: np.ndarray, position = None, count_name=None) :
        x = self._prep_one(bgr_img).unsqueeze(0)  # [1,3,H,W]

        if self.use_cuda:
            x = x.to(device=self.device, non_blocking=True).to(memory_format=torch.channels_last)

        with torch.amp.autocast("cuda", dtype=self.amp_dtype, enabled=self.use_cuda):
            emb = self.model(x)  # [1, 2560]

        emb_np = emb.float().cpu().numpy() if self.use_cuda else emb.numpy()
        emb_z  = (emb_np - self.mean) / self.std   # [1, 2560]

        dists, _ = self.knn.kneighbors(
            emb_z,
            n_neighbors=self.knn.n_neighbors,
            return_distance=True
        )
        score = int(dists.mean())
        status = "GOOD" if score <= self.fixed_threshold else "BAD"
        
        # goodThresholdValuesArray = []

        # if score <= self.fixed_threshold:
        #     status = "GOOD"
        # else:
        #     status = "BAD"
        #     for i in range(1, 6):
        #         goodThresholdValuesArray.append(self.fixed_threshold + i)
        #     if position and count_name:
        #             if score in goodThresholdValuesArray:
        #                 saveFolder = Path(APP_DIR) / "PredictedBadCone" / str(count_name) / str(position)
        #                 saveFolder.mkdir(parents=True, exist_ok=True)

        #                 from datetime import datetime
        #                 ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        #                 savePath = saveFolder / f"Might_Be_Wrongly_predicted_{position}_score_{score}_{ts}.bmp"
        #                 cv2.imwrite(str(savePath), bgr_img)
        #                 print(f"Saved bad cone image to: {savePath}")
        #             else:
        #                 from datetime import datetime
        #                 ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        #                 savePath = saveFolder / f"BAD_predicted_IN_{position}_score_{score}_{ts}.bmp"
        #                 cv2.imwrite(str(savePath), bgr_img)
        #                 print(f"Saved bad cone image to: {savePath}")   
        #     print(goodThresholdValuesArray)        
        return status, score


def prediction_process(prediction_path,threshold,bgr_img, position = None, count_name=None):
    print("[PreB7] using file:", __file__)
    print("[PreB7] position:", position, "count_name:", count_name)
    # Speed knobs
    USE_COMPILE   = True   # try torch.compile on PyTorch 2.x
    WARMUP_ITERS  = 3        # GPU warmup passes
    USE_CPU       = True    # force CPU (debug)

    pred = B7KnnPredictor(
                artifact_path=prediction_path,
                fixed_threshold=threshold,
                use_cpu=USE_CPU,
                use_compile=USE_COMPILE,
                warmup_iters=WARMUP_ITERS
            )
    return pred.predict_single_from_bgr(bgr_img, position, count_name)
