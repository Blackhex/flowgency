"""Pure policy tests for Git evidence publication contracts.

These models do no filesystem or network work; they only validate the shape
of a project's Git publication policy. Later tasks extend this file with
capture and publication behavior.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flowgency.git_evidence.models import (
    GitPublicationPolicy,
    GitRemotePolicy,
    git_policy_digest,
)


def test_local_git_policy_needs_no_remote():
    policy = GitPublicationPolicy(mode="local")
    assert policy.remote is None
    assert policy.allowed_refs == ()


def test_remote_git_policy_cannot_omit_publication_destination():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(mode="remote")


def test_remote_git_policy_requires_allowed_refs():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="remote",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        )


def test_local_git_policy_rejects_a_remote():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="local",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        )


def test_remote_git_policy_rejects_duplicate_allowed_refs():
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="remote",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
            allowed_refs=("refs/heads/main", "refs/heads/main"),
        )


def test_https_remote_accepts_anonymous_and_credential_manager():
    for auth in ("anonymous", "credential-manager"):
        policy = GitRemotePolicy(name="origin", url="https://example.com/repo.git", auth=auth)
        assert policy.auth == auth


def test_ssh_remote_requires_ssh_agent_and_known_hosts():
    policy = GitRemotePolicy(
        name="origin",
        url="ssh://git@example.com/repo.git",
        auth="ssh-agent",
        known_hosts="known_hosts",
    )
    assert policy.auth == "ssh-agent"
    assert str(policy.known_hosts) == "known_hosts"


def test_file_remote_accepts_absolute_path_anonymous():
    policy = GitRemotePolicy(name="origin", url="file:///abs/repo.git")
    assert policy.auth == "anonymous"


@pytest.mark.parametrize(
    "url,auth,known_hosts",
    [
        ("ssh://git@example.com/repo.git", "anonymous", None),
        ("ssh://git@example.com/repo.git", "ssh-agent", None),
        ("ssh://git:pass@example.com/repo.git", "ssh-agent", "known_hosts"),
        ("https://example.com/repo.git", "ssh-agent", None),
        ("https://user@example.com/repo.git", "anonymous", None),
        ("https://user:pass@example.com/repo.git", "anonymous", None),
        ("https://example.com/repo.git?x=1", "anonymous", None),
        ("https://example.com/repo.git#frag", "anonymous", None),
        ("git://example.com/repo.git", "anonymous", None),
        ("ext::sh -c 'touch pwned'", "anonymous", None),
        ("file:relative/repo.git", "anonymous", None),
        ("file:///abs/repo.git", "credential-manager", None),
        ("file:///abs/repo.git", "ssh-agent", None),
        ("https://example.com/repo.git", "anonymous", "known_hosts"),
        ("file://server/share/repo.git", "anonymous", None),
        ("file://localhost/abs/repo.git", "anonymous", None),
    ],
)
def test_invalid_remote_url_combinations_are_rejected(url, auth, known_hosts):
    with pytest.raises(ValidationError):
        GitRemotePolicy(name="origin", url=url, auth=auth, known_hosts=known_hosts)


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "refs/heads/",
        "refs/heads/../main",
        "refs/heads/ma\\in",
        "refs/heads/main@{0}",
        "refs/heads/ma:in",
        "refs/heads/ma*in",
        "refs/heads/*/main",
        "refs/heads/main.lock",
        "refs/heads/.main",
        "refs/heads/main.",
        "/refs/heads/main",
        "refs/heads/main/",
        "main",
        "heads/main",
    ],
)
def test_invalid_allowed_ref_is_rejected(ref):
    with pytest.raises(ValidationError):
        GitPublicationPolicy(
            mode="remote",
            remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
            allowed_refs=(ref,),
        )


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/main",
        "refs/heads/feature/foo",
        "refs/tags/v1.0.0",
        "refs/heads/*",
    ],
)
def test_valid_allowed_ref_is_accepted(ref):
    policy = GitPublicationPolicy(
        mode="remote",
        remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        allowed_refs=(ref,),
    )
    assert policy.allowed_refs == (ref,)


def test_remote_name_rejects_unsafe_subsection():
    with pytest.raises(ValidationError):
        GitRemotePolicy(name='bad"name', url="https://example.com/repo.git")


def test_git_policy_digest_is_none_for_missing_policy():
    assert git_policy_digest(None) is None


def test_git_policy_digest_is_stable_across_equivalent_construction():
    policy_a = GitPublicationPolicy(
        mode="remote",
        remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        allowed_refs=("refs/heads/main", "refs/tags/v1"),
    )
    policy_b = GitPublicationPolicy.model_validate(policy_a.model_dump(mode="json"))
    assert git_policy_digest(policy_a) == git_policy_digest(policy_b)


def test_git_policy_digest_changes_with_policy():
    local = GitPublicationPolicy(mode="local")
    remote = GitPublicationPolicy(
        mode="remote",
        remote=GitRemotePolicy(name="origin", url="https://example.com/repo.git"),
        allowed_refs=("refs/heads/main",),
    )
    assert git_policy_digest(local) != git_policy_digest(remote)
