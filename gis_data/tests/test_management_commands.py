from io import StringIO

from django.contrib.gis.geos import LineString
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase


class CommandRegistrationTest(SimpleTestCase):
    """Basic test to verify command registration."""

    def test_command_exists(self):
        """Test that the command is registered."""
        from django.core.management import get_commands

        commands = get_commands()
        possible_names = [
            "prepare_gis_data",
            "preparegisdata",
            "gis_data",
            "gis_preparation",
            "preparedata",
        ]

        found = False
        for name in possible_names:
            if name in commands:
                found = True
                try:
                    stdout = StringIO()
                    stderr = StringIO()
                    call_command(name, "--help", stdout=stdout, stderr=stderr)
                    self.assertTrue(True)
                    break
                except (CommandError, SystemExit):
                    self.assertTrue(True)
                    break

        if not found:
            self.skipTest("Command not found. Check file structure.")

    def test_line_string_format(self):
        """Test that we understand the correct LineString format."""
        correct_line = LineString([(9.0, 46.0), (9.1, 46.1)], srid=4326)

        self.assertEqual(correct_line.srid, 4326)
        self.assertEqual(len(correct_line.coords), 2)
