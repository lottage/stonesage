import unittest
from prompt_toolkit.document import Document
from harness.cli.completer import HarnessCompleter, PROMPT_TOOLKIT_AVAILABLE


class TestCLIAutocomplete(unittest.TestCase):
    """
    Unit test suite verifying interactive CLI slash command auto-fill,
    real-time narrowing dropdown popup options, subcommands, and tab-completion.
    """

    def setUp(self):
        self.completer = HarnessCompleter()

    def test_prompt_toolkit_installed(self):
        self.assertTrue(PROMPT_TOOLKIT_AVAILABLE, "prompt_toolkit must be installed")

    def test_root_slash_autocomplete(self):
        """Typing '/' displays all available slash commands with descriptions."""
        doc = Document("/")
        completions = list(self.completer.get_completions(doc, None))
        cmd_names = [c.text for c in completions]
        self.assertIn("/agent", cmd_names)
        self.assertIn("/models", cmd_names)
        self.assertIn("/streams", cmd_names)
        self.assertIn("/nudge", cmd_names)
        self.assertIn("/mem", cmd_names)
        self.assertIn("/train", cmd_names)
        self.assertIn("/policy", cmd_names)
        self.assertIn("/exit", cmd_names)

    def test_prefix_narrowing(self):
        """Typing '/ag' narrows down to '/agent'."""
        doc = Document("/ag")
        completions = list(self.completer.get_completions(doc, None))
        cmd_names = [c.text for c in completions]
        self.assertIn("/agent", cmd_names)
        self.assertNotIn("/models", cmd_names)
        self.assertNotIn("/streams", cmd_names)

    def test_agent_command_suggestions(self):
        """Typing '/agent' immediately surfaces subcommands in popup menu."""
        doc = Document("/agent")
        completions = list(self.completer.get_completions(doc, None))
        cmd_names = [c.text for c in completions]
        self.assertIn("/agent", cmd_names)
        self.assertIn("/agent list", cmd_names)
        self.assertIn("/agent build", cmd_names)
        self.assertIn("/agent status", cmd_names)

    def test_agent_subcommand_with_space(self):
        """Typing '/agent ' surfaces subcommands (list, build, status, select)."""
        doc = Document("/agent ")
        completions = list(self.completer.get_completions(doc, None))
        cmd_names = [c.text for c in completions]
        self.assertIn("list", cmd_names)
        self.assertIn("build", cmd_names)
        self.assertIn("status", cmd_names)
        self.assertIn("select", cmd_names)

    def test_agent_subcommand_narrowing(self):
        """Typing '/agent bu' narrows down strictly to 'build'."""
        doc = Document("/agent bu")
        completions = list(self.completer.get_completions(doc, None))
        cmd_names = [c.text for c in completions]
        self.assertEqual(cmd_names, ["build"])

    def test_policy_subcommands(self):
        """Typing '/policy ' surfaces security modes (strict, tiered, autonomous)."""
        doc = Document("/policy ")
        completions = list(self.completer.get_completions(doc, None))
        cmd_names = [c.text for c in completions]
        self.assertIn("strict", cmd_names)
        self.assertIn("tiered", cmd_names)
        self.assertIn("autonomous", cmd_names)

    def test_agent_select_autocomplete(self):
        """Typing '/agent select ' autocompletes available registered agent IDs."""
        doc = Document("/agent select ")
        completions = list(self.completer.get_completions(doc, None))
        cmd_names = [c.text for c in completions]
        self.assertIn("aevum", cmd_names)

    def test_agent_shell_list_and_select(self):
        """Verify AgentShell list_agents and select_agent functions without error."""
        from harness.cli.agent_shell import AgentShell
        from harness.core.openclaw_engine import openclaw_engine

        profiles = openclaw_engine.list_profiles()
        self.assertTrue(len(profiles) >= 1)
        self.assertTrue(any(p["agent_id"] == "aevum" for p in profiles))

        # Select aevum
        AgentShell.select_agent(["aevum"])
        self.assertIsNotNone(AgentShell.active_agent)
        self.assertEqual(AgentShell.active_agent["agent_id"], "aevum")
        self.assertEqual(AgentShell.active_agent["name"], "Aevum")


if __name__ == "__main__":
    unittest.main()

