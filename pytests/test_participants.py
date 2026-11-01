import http.client
import json
import threading

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.core import AuthenticationError, ConflictError, ValidationError

TARGET = "knowledge/entities/org-acme.md"


def high_risk(service, actor, key):
    return service.propose(actor=actor, target_source=TARGET, risk="high", idempotency_key=key,
                           patch={"op": "replace", "path": "/base/node/label", "value": f"Acme {key}"}, reason="verified correction")["data"]


@pytest.fixture
def people(service):
    p = service.participants
    owner = p.setup(username="ricky", label="Ricky", password="correct horse")
    other = p.create_human(username="lin", label="Lin", password="another pass", roles=["reviewer"], actor=owner["id"])
    agent = p.create_agent(slug="researcher", label="研究员", owner=owner["id"], roles=["agent"], actor=owner["id"], host="claude-code", model="claude")
    sibling = p.create_agent(slug="checker", label="核验员", owner=owner["id"], roles=["agent"], actor=owner["id"])
    return owner, other, agent, sibling


def test_setup_is_once_and_login_issues_expiring_session(service, people):
    p = service.participants
    assert not p.setup_required()
    with pytest.raises(ConflictError):
        p.setup(username="again", label="x", password="password1")
    with pytest.raises(AuthenticationError, match="不正确"):
        p.login("ricky", "wrong password")
    session = p.login("ricky", "correct horse", "Studio")
    assert session["token"].startswith("kos_") and session["kind"] == "session" and session["expires_at"]
    principal = p.authenticate(session["token"], "Studio")
    assert principal["id"] == "user:ricky" and principal["kind"] == "human" and "manage" in principal["permissions"]
    stored = service.db.first("SELECT token_hash FROM credential WHERE id=?", (session["id"],))["token_hash"]
    assert session["token"] not in stored  # only the digest is kept
    p.revoke(session["id"])
    with pytest.raises(AuthenticationError, match="吊销"):
        p.authenticate(session["token"])


def test_login_lockout_after_repeated_failures(service, people):
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            service.participants.login("lin", "nope")
    with pytest.raises(AuthenticationError, match="次数过多"):
        service.participants.login("lin", "another pass")


def test_agents_cannot_hold_approval_roles_and_credentials_are_scoped(service, people):
    owner, _, agent, _ = people
    p = service.participants
    with pytest.raises(ValidationError, match="审批与发布由人完成"):
        p.create_agent(slug="approver", label="x", owner=owner["id"], roles=["admin"], actor=owner["id"])
    with pytest.raises(ValidationError, match="负责人"):
        p.create_agent(slug="orphan", label="x", owner=agent["id"], roles=["agent"], actor=owner["id"])
    issued = p.issue(agent["id"], label="Claude Code", actor=owner["id"], scopes=["read"])
    principal = p.authenticate(issued["token"], "claude-code/2.1")
    assert principal["permissions"] == ["read"] and principal["owner"] == "user:ricky"
    with pytest.raises(ValidationError, match="subset"):
        p.issue(agent["id"], label="x", actor=owner["id"], scopes=["review"])
    listed = {item["id"]: item for item in p.directory()["participants"]}
    assert listed["agent:researcher"]["credentials"][0]["last_client"] == "claude-code/2.1"
    p.update(agent["id"], {"status": "disabled"}, owner["id"])
    with pytest.raises(AuthenticationError, match="停用"):
        p.authenticate(issued["token"])


def test_independence_follows_the_accountability_chain(service, people):
    owner, other, agent, sibling = people
    p = service.participants
    assert not p.independent(owner["id"], agent["id"]) and not p.independent(sibling["id"], agent["id"])
    assert p.independent(other["id"], agent["id"])
    proposal = high_risk(service, agent["id"], "agent")
    # Personal mode: the owner is the human check on their own agents' work.
    assert p.review_decision(owner["id"], agent["id"], "high")["basis"] == "owner_of_agent"
    # Organization mode: the owner and their agent are one responsible party.
    p.set_policy({"mode": "organization"}, owner["id"])
    with pytest.raises(ValidationError, match="independent reviewer"):
        service.review_changeset(id=proposal["id"], reviewer=owner["id"], decision="approved", note="ok")
    preview = service.review_batch_preview(ids=[proposal["id"]], reviewer=owner["id"], decision="approved")["data"]
    assert preview["can_review"] is False and "独立责任方" in preview["items"][0]["blockers"][0]
    # Another person may approve.
    assert service.review_changeset(id=proposal["id"], reviewer=other["id"], decision="approved", note="ok")["data"]["status"] == "approved"
    event = service.ledger.events(target_id=proposal["id"], event_type="changeset_approved")[0]
    assert event["payload_json"]["approval_basis"] == "independent"


def test_personal_owner_approves_own_agents_without_self_review(service, people):
    owner, _, agent, sibling = people
    proposal = high_risk(service, agent["id"], "owner")
    preview = service.review_batch_preview(ids=[proposal["id"]], reviewer=owner["id"], decision="approved")["data"]
    assert preview["can_review"] is True
    service.review_changeset(id=proposal["id"], reviewer=owner["id"], decision="approved", note="已核对来源")
    event = service.ledger.events(target_id=proposal["id"], event_type="changeset_approved")[0]
    assert event["payload_json"]["approval_basis"] == "owner_of_agent" and event["payload_json"]["self_review"] is False
    # The owner's own high-risk change still needs another party unless self review is enabled.
    own = high_risk(service, owner["id"], "own")
    with pytest.raises(ValidationError, match="independent reviewer"):
        service.review_changeset(id=own["id"], reviewer=owner["id"], decision="approved", note="ok")


def test_personal_self_review_policy_is_explicit_and_marked(service, people):
    owner, _, agent, _ = people
    p = service.participants
    proposal = high_risk(service, owner["id"], "self")
    p.set_policy({"self_review_high_risk": True}, owner["id"])
    preview = service.review_batch_preview(ids=[proposal["id"]], reviewer=owner["id"], decision="approved")["data"]
    assert preview["items"][0]["blockers"] == ["批量审核不支持自审，请逐条审核"]
    service.review_changeset(id=proposal["id"], reviewer=owner["id"], decision="approved", note="已核对来源")
    row = service.db.first("SELECT review_note FROM changeset WHERE id=?", (proposal["id"],))
    assert row["review_note"].startswith("［个人模式自审］")
    event = service.ledger.events(target_id=proposal["id"], event_type="changeset_approved")[0]
    assert event["payload_json"]["self_review"] is True and event["payload_json"]["accountable_actor"] == "user:ricky"
    with pytest.raises(ValidationError, match="personal mode"):
        p.set_policy({"mode": "organization"}, owner["id"])
    p.set_policy({"self_review_high_risk": False, "mode": "organization"}, owner["id"])
    assert p.policy() == {"mode": "organization", "self_review_high_risk": False, "human_final_approval": True}


def test_agents_never_give_final_approval_or_publish(service, people):
    _, other, agent, sibling = people
    proposal = high_risk(service, agent["id"], "final")
    assert service.participants.review_decision(sibling["id"], agent["id"], "normal")["allowed"] is False
    with pytest.raises(ValidationError, match="由人完成"):
        service.apply_changeset(id=proposal["id"], publisher=sibling["id"])
    with pytest.raises(ValidationError, match="由人完成"):
        service.publish_changeset(id=proposal["id"], publisher=sibling["id"], source_revision="x")


def test_event_cursor_returns_only_governance_events_in_order(service, people):
    owner, other, agent, _ = people
    start = service.events(0, 500)["data"]
    proposal = high_risk(service, agent["id"], "events")
    service.review_changeset(id=proposal["id"], reviewer=other["id"], decision="rejected", note="来源不足")
    page = service.events(start["cursor"], 1)["data"]
    assert [item["type"] for item in page["events"]] == ["agent_proposal"] and page["has_more"] is True
    rest = service.events(page["cursor"])["data"]
    assert [(item["type"], item["actor"]) for item in rest["events"]] == [("changeset_rejected", "user:lin")]
    assert rest["has_more"] is False and service.events(rest["cursor"])["data"]["events"] == []
    with pytest.raises(ValidationError):
        service.events("x")


def test_inbox_puts_what_i_may_approve_first(service, people):
    owner, other, agent, _ = people
    mine = high_risk(service, owner["id"], "inbox-mine")
    from_agent = high_risk(service, agent["id"], "inbox-agent")
    theirs = high_risk(service, other["id"], "inbox-theirs")
    inbox = service.inbox(owner["id"])["data"]
    assert [item["id"] for item in inbox["items"]] == [from_agent["id"], theirs["id"], mine["id"]]
    assert inbox["items"][0]["actor_info"]["owner"] == "user:ricky" and inbox["items"][0]["governance"]["basis"] == "owner_of_agent"
    assert inbox["items"][2]["governance"]["allowed"] is False
    assert inbox["counts"] == {"total": 3, "can_approve": 2, "from_my_agents_or_me": 2}
    assert inbox["items"][0]["patch"] == {"op": "replace"} and "operations" not in inbox["items"][0]


def call(port, method, path, token=None, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    sent = {**(headers or {})}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        sent.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
    connection.request(method, path, payload, sent)
    response = connection.getresponse()
    data = json.loads(response.read() or b"{}")
    connection.close()
    return response.status, data


def test_account_and_agent_lifecycle_over_http(service):
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({"dev": {"principal": "local:admin", "roles": ["admin"]}})})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        assert call(port, "GET", "/v1/auth/status") == (200, {"data": {"setup_required": True, "policy_mode": "personal"}})
        status, body = call(port, "POST", "/v1/auth/setup", body={"username": "ricky", "label": "Ricky", "password": "correct horse"})
        assert status == 201 and body["data"]["participant"]["id"] == "user:ricky"
        session = body["data"]["token"]
        assert call(port, "POST", "/v1/auth/setup", body={"username": "x", "password": "password1"})[0] == 409
        status, body = call(port, "GET", "/v1/session", session)
        assert body["kind"] == "human" and body["label"] == "Ricky" and body["policy"]["mode"] == "personal"
        status, body = call(port, "POST", "/v1/participants/agents", session, {"id": "researcher", "label": "研究员", "roles": ["agent"], "host": "cursor"})
        assert status == 200 and body["data"]["owner"] == "user:ricky"
        status, body = call(port, "POST", "/v1/participants/credentials", session, {"participant_id": "agent:researcher", "label": "Cursor"})
        agent_token = body["data"]["token"]
        status, body = call(port, "GET", "/v1/session", agent_token, headers={"X-KnowledgeOS-Client": "cursor/1.0"})
        assert body["principal"] == "agent:researcher" and "manage" not in body["permissions"] and "propose" in body["permissions"]
        assert call(port, "GET", "/v1/participants", agent_token)[0] == 403
        assert call(port, "GET", "/v1/inbox", agent_token)[0] == 403
        status, body = call(port, "POST", "/v1/propose", agent_token, {"target_source": TARGET, "risk": "high", "reason": "verified",
                                                                         "patch": {"op": "replace", "path": "/base/node/label", "value": "Acme"}})
        assert status == 201
        status, body = call(port, "GET", "/v1/changesets", session)
        item = body["data"][0]
        assert item["actor_info"]["kind"] == "agent" and item["governance"]["basis"] == "owner_of_agent"
        status, body = call(port, "GET", "/v1/events?after=0", agent_token)
        assert status == 200 and any(event["type"] == "agent_proposal" and event["actor"] == "agent:researcher" for event in body["data"]["events"])
        status, body = call(port, "GET", "/v1/participants", session)
        researcher = next(item for item in body["data"]["participants"] if item["id"] == "agent:researcher")
        assert researcher["credentials"][0]["last_client"] == "cursor/1.0" and researcher["changesets"] == {"review_required": 1}
        status, body = call(port, "POST", "/v1/participants/policy", agent_token, {"changes": {"self_review_high_risk": True}})
        assert status == 403
        status, body = call(port, "POST", "/v1/participants/policy", session, {"changes": {"self_review_high_risk": True}})
        assert status == 200 and body["data"]["self_review_high_risk"] is True
        assert call(port, "POST", "/v1/auth/password", session, {"current": "bad", "new": "new password"})[0] == 401
        assert call(port, "POST", "/v1/auth/password", session, {"current": "correct horse", "new": "new password"})[0] == 200
        assert call(port, "POST", "/v1/auth/login", body={"username": "ricky", "password": "new password"})[0] == 200
        assert call(port, "POST", "/v1/participants/credentials/revoke", session, {"id": researcher["credentials"][0]["id"]})[0] == 200
        assert call(port, "GET", "/v1/session", agent_token)[0] == 401
        assert call(port, "POST", "/v1/auth/logout", session, {})[0] == 200
        assert call(port, "GET", "/v1/session", session)[0] == 401
        assert call(port, "GET", "/v1/session", "dev")[0] == 200  # configured tokens keep working
    finally:
        server.shutdown()
        server.server_close()
