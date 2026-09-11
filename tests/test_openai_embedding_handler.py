import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


def load_openai_embedding_handler():
    """Load the handler without importing Newelle's GTK-dependent package tree."""
    module_names = (
        "src.handlers",
        "src.handlers.embeddings",
        "src.handlers.embeddings.embedding",
        "numpy",
    )
    previous_modules = {name: sys.modules.get(name) for name in module_names}

    handlers = types.ModuleType("src.handlers")
    handlers.ExtraSettings = object

    embeddings = types.ModuleType("src.handlers.embeddings")
    embeddings.__path__ = []

    embedding = types.ModuleType("src.handlers.embeddings.embedding")

    class EmbeddingHandler:
        pass

    embedding.EmbeddingHandler = EmbeddingHandler
    embedding.EmbeddingPurpose = object

    numpy = types.ModuleType("numpy")
    numpy.ndarray = object
    sys.modules.update(
        {
            "src.handlers": handlers,
            "src.handlers.embeddings": embeddings,
            "src.handlers.embeddings.embedding": embedding,
            "numpy": numpy,
        }
    )

    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "handlers"
        / "embeddings"
        / "openai_handler.py"
    )
    module_name = "src.handlers.embeddings.openai_handler"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module.OpenAIEmbeddingHandler


class EmbeddingResult:
    shape = (1, 1536)

    def __len__(self):
        return self.shape[0]


class OpenAIEmbeddingHandlerTests(unittest.TestCase):
    def test_custom_model_uses_embedding_vector_dimension(self):
        handler_class = load_openai_embedding_handler()
        handler = handler_class.__new__(handler_class)
        handler.get_setting = Mock(return_value="custom-embedding-model")
        handler.get_embedding = Mock(return_value=EmbeddingResult())

        self.assertEqual(handler.get_embedding_size(), 1536)
        handler.get_embedding.assert_called_once_with([""])


if __name__ == "__main__":
    unittest.main()
