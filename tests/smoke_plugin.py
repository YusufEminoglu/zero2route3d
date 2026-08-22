"""Smoke test for 02Route 3D plugin loading and UI factory."""
import unittest
from unittest.mock import MagicMock

from zero2route3d import classFactory
from zero2route3d.main_plugin import Route3DPlugin


class TestPluginSmoke(unittest.TestCase):
    """Verify classFactory and main plugin lifecycle."""

    def test_class_factory(self):
        mock_iface = MagicMock()
        plugin = classFactory(mock_iface)
        self.assertIsInstance(plugin, Route3DPlugin)
        self.assertEqual(plugin.iface, mock_iface)


if __name__ == "__main__":
    unittest.main()
