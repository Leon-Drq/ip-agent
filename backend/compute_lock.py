"""Serialize local GPU workloads across video generation and transcription."""
import threading

gpu_lock = threading.Lock()
