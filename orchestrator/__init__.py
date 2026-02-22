"""
Data Orchestration Tool
========================

Production-grade orchestration for medallion architecture data pipelines.
Uses Prefect as the orchestration control plane and LangGraph as the
execution engine within each pipeline layer.

Layers:
    PreBronze → Bronze → Silver → Gold → Neo4j
"""

__version__ = "0.1.0"
