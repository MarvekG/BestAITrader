import ast
from typing import Optional


BLOCKED_IMPORTS = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "pathlib",
    "shutil",
    "tempfile",
    "ctypes",
    "importlib",
    "builtins",
    "multiprocessing",
}
BLOCKED_CALLS = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "help",
    "dir",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "breakpoint",
}
BLOCKED_NAMES = BLOCKED_IMPORTS | {"__builtins__"}
BLOCKED_ATTRIBUTES = {
    "__class__",
    "__bases__",
    "__mro__",
    "__subclasses__",
    "__globals__",
    "__code__",
    "__closure__",
    "__func__",
    "__self__",
}
_DISALLOWED_NODE_TYPES = [
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
    ast.ClassDef,
    ast.With,
]
if hasattr(ast, "Match"):
    _DISALLOWED_NODE_TYPES.append(ast.Match)
DISALLOWED_NODE_TYPES = tuple(_DISALLOWED_NODE_TYPES)


class SandboxValidationError(Exception):
    """用户代码违反沙箱静态校验规则。"""


def _module_name(module_name: Optional[str]) -> str:
    """
    提取导入模块根名称。

    Args:
        module_name: AST 中记录的模块名称。

    Returns:
        模块根名称；空值会返回空字符串。
    """
    return (module_name or "").split(".", 1)[0]


class SandboxValidator(ast.NodeVisitor):
    """拒绝可能逃逸计算沙箱的 Python 语法。"""

    def generic_visit(self, node: ast.AST) -> None:
        """
        检查通用 AST 节点是否属于禁用语法。

        Args:
            node: 当前遍历到的 AST 节点。

        Raises:
            SandboxValidationError: 当前节点类型被禁用。
        """
        if isinstance(node, DISALLOWED_NODE_TYPES):
            raise SandboxValidationError(f"Unsupported syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """
        检查普通 import 是否引用禁用模块。

        Args:
            node: import AST 节点。

        Raises:
            SandboxValidationError: 导入了禁用模块。
        """
        for alias in node.names:
            module_name = _module_name(alias.name)
            if module_name in BLOCKED_IMPORTS:
                raise SandboxValidationError(f"Import not allowed: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """
        检查 from import 是否引用禁用模块。

        Args:
            node: from import AST 节点。

        Raises:
            SandboxValidationError: 导入了禁用模块。
        """
        module_name = _module_name(node.module)
        if module_name in BLOCKED_IMPORTS:
            raise SandboxValidationError(f"Import not allowed: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """
        检查函数调用是否命中禁用内置函数。

        Args:
            node: 函数调用 AST 节点。

        Raises:
            SandboxValidationError: 调用了禁用函数。
        """
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            raise SandboxValidationError(f"Call not allowed: {node.func.id}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """
        检查变量名称是否命中禁用名称。

        Args:
            node: 名称 AST 节点。

        Raises:
            SandboxValidationError: 访问了禁用名称。
        """
        if node.id in BLOCKED_NAMES:
            raise SandboxValidationError(f"Name not allowed: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """
        检查属性访问是否命中特殊逃逸属性。

        Args:
            node: 属性访问 AST 节点。

        Raises:
            SandboxValidationError: 访问了禁用属性。
        """
        if node.attr in BLOCKED_ATTRIBUTES or node.attr.startswith("__") or node.attr.endswith("__"):
            raise SandboxValidationError(f"Attribute access not allowed: {node.attr}")
        self.generic_visit(node)


def validate_python_code(code: str) -> None:
    """
    对用户 Python 代码执行静态安全校验。

    Args:
        code: 待执行的 Python 源码。

    Raises:
        SandboxValidationError: 代码包含禁用语法、模块、函数或属性。
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxValidationError(f"Invalid Python syntax: {exc.msg}") from exc
    SandboxValidator().visit(tree)
