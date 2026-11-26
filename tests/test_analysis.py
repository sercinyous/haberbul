from types import SimpleNamespace

from gundem.analysis import TopicAnalyzer
from gundem.models import Item, Topic


class FakeClient:
    def __init__(self, text=None, error=None):
        self.calls = []
        self._text, self._error = text, error
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self._text)])


def make():
    t = Topic(title="Ünlü şarkıcı konser verdi", lang="tr", x_queries=[])
    items = [Item(title="Şarkıcı konser verdi", summary="Binlerce kişi izledi")]
    return t, items


def test_analyze_writes_fields():
    client = FakeClient('{"category": "unlu", "fit": 8, "x_queries": ["şarkıcı", "#konser"], "sensitive": false}')
    t, items = make()
    result = TopicAnalyzer(client, "fast-model").analyze(t, items)
    assert result["category"] == "unlu"
    assert (t.category, t.fit_score, t.x_queries) == ("unlu", 8, ["şarkıcı", "#konser"])
    assert t.sensitive is not True
    call = client.calls[0]
    assert call["model"] == "fast-model"
    assert call["max_tokens"] == 300
    assert call["timeout"] == 15
    assert "Ünlü şarkıcı konser verdi" in call["messages"][0]["content"]


def test_analyze_parses_fenced_json_and_clamps():
    client = FakeClient('```json\n{"category": "futbol", "fit": 99, "x_queries": ["a", "", "b", "c", "d"], "sensitive": true}\n```')
    t, items = make()
    TopicAnalyzer(client, "m").analyze(t, items)
    assert t.fit_score == 10
    assert t.x_queries == ["a", "b", "c"]
    assert t.sensitive is True


def test_analyze_sensitive_false_does_not_clear():
    client = FakeClient('{"category": "dunya", "fit": -3, "x_queries": [], "sensitive": false}')
    t, items = make()
    t.sensitive = True
    TopicAnalyzer(client, "m").analyze(t, items)
    assert t.sensitive is True
    assert t.fit_score == 0


def test_analyze_invalid_category_falls_back_to_diger():
    client = FakeClient('{"category": "uzay", "fit": 5, "x_queries": ["x"], "sensitive": false}')
    t, items = make()
    result = TopicAnalyzer(client, "m").analyze(t, items)
    assert result["category"] == "diger"
    assert t.category == "diger"


def test_analyze_error_returns_none():
    t, items = make()
    assert TopicAnalyzer(FakeClient(error=RuntimeError("boom")), "m").analyze(t, items) is None
    assert t.category is None and t.fit_score is None and t.x_queries == []


def test_analyze_bad_json_returns_none():
    t, items = make()
    assert TopicAnalyzer(FakeClient("not json"), "m").analyze(t, items) is None
    assert t.category is None
