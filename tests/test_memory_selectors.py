import hashlib
import json
import unicodedata

import pytest

from agency.configuration.models import MemoryChannel, MemorySelector
from agency.memory import resolve_memory_selector, select_effective_memory


def test_routine_selector_is_stable_across_runs(tmp_path):
    first = resolve_memory_selector(
        MemorySelector(scope="routine"),
        job_id="job-a",
        team_key="news",
        agent_name="advisor",
        routine_id="daily-review",
        channels={},
        store_root=tmp_path,
    )
    second = resolve_memory_selector(
        MemorySelector(scope="routine"),
        job_id="job-b",
        team_key="news",
        agent_name="advisor",
        routine_id="daily-review",
        channels={},
        store_root=tmp_path,
    )

    assert first.memory_hash == second.memory_hash


def test_run_selector_is_unique_per_job(tmp_path):
    selector = MemorySelector(scope="run")

    first = resolve_memory_selector(
        selector,
        job_id="job-a",
        team_key="news",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=tmp_path,
    )
    second = resolve_memory_selector(
        selector,
        job_id="job-b",
        team_key="news",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=tmp_path,
    )

    assert first.memory_hash != second.memory_hash


def test_selector_uses_exact_canonical_json_and_domain_prefix(tmp_path):
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="job-a",
        team_key="news",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=tmp_path,
    )

    expected_json = json.dumps(
        {"agent": "advisor", "team": "news", "scope": "agent", "version": 1},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected_hash = hashlib.sha256(
        b"agency-memory:v1\0" + expected_json.encode("utf-8")
    ).hexdigest()

    assert resolved.canonical_json == expected_json
    assert resolved.memory_hash == expected_hash
    assert resolved.directory == tmp_path.resolve() / expected_hash


def test_channel_selector_uses_only_declared_global_channel(tmp_path):
    channels = {"support": MemoryChannel(display_name="Support")}
    expected = '{"channel":"support","scope":"channel","version":1}'

    first = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="job-a",
        team_key="news",
        agent_name="advisor",
        routine_id=None,
        channels=channels,
        store_root=tmp_path,
    )
    second = resolve_memory_selector(
        MemorySelector(scope="channel", channel="support"),
        job_id="job-b",
        team_key="ops",
        agent_name="builder",
        routine_id="other-routine",
        channels=channels,
        store_root=tmp_path,
    )

    assert first.canonical_json == expected
    assert second.canonical_json == expected
    assert second.memory_hash == first.memory_hash


def test_channel_selector_rejects_unknown_channel(tmp_path):
    with pytest.raises(
        ValueError,
        match="unknown global memory channel: missing",
    ):
        resolve_memory_selector(
            MemorySelector(scope="channel", channel="missing"),
            job_id="job-a",
            team_key="news",
            agent_name="advisor",
            routine_id=None,
            channels={},
            store_root=tmp_path,
        )


@pytest.mark.parametrize("scope", ["run", "routine", "agent", "team"])
@pytest.mark.parametrize("channel", ["support", "   "])
def test_non_channel_selectors_reject_channel_field(tmp_path, scope, channel):
    with pytest.raises(ValueError, match="channel field is only valid for channel scope"):
        resolve_memory_selector(
            MemorySelector(scope=scope, channel=channel),
            job_id="job-a",
            team_key="news",
            agent_name="advisor",
            routine_id="daily-review",
            channels={"support": MemoryChannel(display_name="Support")},
            store_root=tmp_path,
        )


def test_case_collision_key_is_unicode_normalized_and_casefolded(tmp_path):
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="job-a",
        team_key="news",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=tmp_path,
    )
    from agency.memory import MemoryStore

    store = MemoryStore(tmp_path)
    seeded = store.ensure(resolved)
    composed = "M\u00e9mory.md"
    decomposed = unicodedata.normalize("NFD", composed)

    with pytest.raises(ValueError, match="case-fold"):
        store.try_save(
            resolved,
            seeded.revision,
            {composed: b"a", decomposed: b"b"},
        )


def test_routine_selector_requires_routine_id(tmp_path):
    with pytest.raises(
        ValueError,
        match="routine memory requires a routine ID",
    ):
        resolve_memory_selector(
            MemorySelector(scope="routine"),
            job_id="job-a",
            team_key="news",
            agent_name="advisor",
            routine_id=None,
            channels={},
            store_root=tmp_path,
        )


def test_effective_selector_precedence_prefers_manual_override():
    manual = MemorySelector(scope="channel", channel="support")
    routine = MemorySelector(scope="routine")
    default = MemorySelector(scope="agent")

    assert select_effective_memory(manual, routine, default) == manual


def test_effective_selector_precedence_prefers_routine_over_agent_default():
    routine = MemorySelector(scope="routine")
    default = MemorySelector(scope="agent")

    assert select_effective_memory(None, routine, default) == routine


def test_effective_selector_falls_back_to_implicit_run():
    assert select_effective_memory(
        None,
        None,
        None,
    ) == MemorySelector(scope="run")


def test_team_memory_selector_uses_team_scope_and_criteria(tmp_path):
    selector = MemorySelector(scope="team")

    resolved = resolve_memory_selector(
        selector,
        job_id="job-1",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels={},
        store_root=tmp_path,
    )

    assert json.loads(resolved.canonical_json) == {
        "scope": "team",
        "team": "newsletter",
        "version": 1,
    }


def test_group_memory_scope_is_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MemorySelector(scope="group")


def test_canonical_json_with_group_key_is_rejected(tmp_path):
    from agency.memory.selectors import resolved_memory_from_canonical
    canonical = '{"group":"newsletter","scope":"team","version":1}'
    with pytest.raises(ValueError, match="canonical criteria keys are invalid"):
        resolved_memory_from_canonical(
            MemorySelector(scope="team"),
            canonical,
            store_root=tmp_path,
        )

