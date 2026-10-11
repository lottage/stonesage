"""What Computer sends to the model, captured with fakes (for tests/test_agents.py's "Computer's prompt is unchanged" pin).

Uses only the loop's long-standing API, so the same file can be run against an older checkout to make the golden
(tests/fixtures/computer_prompt_golden.json): extract the old tree, copy this file into its tests/, call capture().
"""

import copy
from datetime import datetime
from unittest import mock

import agent_fixture
from agent_fixture import reply
from stonesage.courage import prompt

NOW = datetime(2026, 10, 11, 12, 0)


class FakeMemory:
    def recall(self, text):
        return [{"id": "n1", "score": 0.5, "at": 1790000000.0, "text": "You like tea."}]

    def card(self, notes):
        return "[memory] " + " ".join(n["text"] for n in notes)

    def learn_later(self, *a, **k):
        pass


def capture():
    """The request bodies of three turns: a first message, a mid-conversation message (history + the fresh-facts reminder),
    and a turn on the spare brain's wording is not included. Everything the model is sent: messages, tools, flags."""
    out = []
    with mock.patch.object(prompt, "datetime") as dt:
        dt.now.return_value = NOW
        for history, text in (([], "What is the temperature inside?"),
                              ([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello."},
                                {"role": "user", "content": "thanks"}, {"role": "assistant", "content": "Anytime."}],
                               "Where is Bella?")):
            llm = agent_fixture.ScriptedLLM(reply("Fine."))
            agent, _ = agent_fixture.make_agent(llm)
            agent.persona_fn = lambda: "You are Computer."
            agent.runs_on_fn = lambda: "an example GPU"
            agent.memory = FakeMemory()
            agent_fixture.run(agent, text, history=copy.deepcopy(history))
            out.append(llm.requests[0])
    return out
