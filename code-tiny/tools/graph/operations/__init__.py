"""
Graph Operations Module

Contains domain-specific operations for different entity types in the graph.
Each operation module focuses on a specific bounded context.
"""

from tools.graph.operations.function_ops import FunctionNodeOperations
from tools.graph.operations.document_ops import DocumentNodeOperations
from tools.graph.operations.infra_ops import InfraNodeOperations
from tools.graph.operations.cross_edge_ops import CrossEdgeOperations
from tools.graph.operations.package_ops import PackageNodeOperations
from tools.graph.operations.class_ops import ClassNodeOperations
from tools.graph.operations.namespace_ops import NamespaceNodeOperations
from tools.graph.operations.type_ops import TypeNodeOperations

__all__ = [
    'FunctionNodeOperations',
    'DocumentNodeOperations',
    'InfraNodeOperations',
    'CrossEdgeOperations',
    'PackageNodeOperations',
    'ClassNodeOperations',
    'NamespaceNodeOperations',
    'TypeNodeOperations',
]
