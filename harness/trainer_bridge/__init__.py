"""
Trainer Bridge Package for Unified LLM Harness.
Integrates Unsloth/QLoRA training loops, Hugging Face GGUF Hub browsing,
and the 70/30 ground-truth dataset curation pipeline.
"""

from .trainer_hub import TrainerHub, TrainingJobConfig
from .hf_browser import HuggingFaceBrowser, GGUFModelInfo

__all__ = ["TrainerHub", "TrainingJobConfig", "HuggingFaceBrowser", "GGUFModelInfo"]
