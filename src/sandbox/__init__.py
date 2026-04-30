"""Sandbox package — Stage 5.

Provides Docker-backed ephemeral container execution for all tool runners.
"""
from src.sandbox.manager import ContainerRef, SandboxManager, get_sandbox_manager

__all__ = ["ContainerRef", "SandboxManager", "get_sandbox_manager"]
