import uuid

from app.db.models import Budget


def budget_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category": "Food",
        "limit_amount": "250.00",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
    }
    payload.update(overrides)
    return payload


def test_budget_crud_persists_and_returns_owned_data(api_client) -> None:
    client, user, database = api_client

    created = client.post("/api/budgets", json=budget_payload())
    assert created.status_code == 201
    budget = created.json()
    assert budget["category"] == "Food"
    assert budget["user_id"] == str(user.id)

    listed = client.get("/api/budgets")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [budget["id"]]

    fetched = client.get(f"/api/budgets/{budget['id']}")
    assert fetched.status_code == 200

    updated = client.patch(
        f"/api/budgets/{budget['id']}",
        json={"category": "Groceries", "limit_amount": "300.00"},
    )
    assert updated.status_code == 200
    assert updated.json()["category"] == "Groceries"
    assert updated.json()["limit_amount"] == "300.00"

    with database() as db:
        persisted = db.get(Budget, uuid.UUID(budget["id"]))
        assert persisted is not None
        assert persisted.limit_amount == 300

    deleted = client.delete(f"/api/budgets/{budget['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/budgets/{budget['id']}").status_code == 404


def test_budget_rejects_invalid_amount_and_period(api_client) -> None:
    client, _, _ = api_client

    assert client.post("/api/budgets", json=budget_payload(limit_amount="0")).status_code == 422
    assert (
        client.post(
            "/api/budgets",
            json=budget_payload(period_start="2026-02-01", period_end="2026-01-31"),
        ).status_code
        == 422
    )


def test_budget_isolation_and_unauthenticated_access(api_client) -> None:
    client, user, database = api_client
    created = client.post("/api/budgets", json=budget_payload()).json()

    from app.api.deps import get_current_user

    other_user = type(user)(id=uuid.uuid4(), email="other@example.com", password_hash="hashed")
    with database.begin() as db:
        db.add(other_user)

    client.app.dependency_overrides[get_current_user] = lambda: other_user
    assert client.get("/api/budgets").json() == []
    assert client.get(f"/api/budgets/{created['id']}").status_code == 404
    assert client.patch(f"/api/budgets/{created['id']}", json={"category": "Other"}).status_code == 404
    assert client.delete(f"/api/budgets/{created['id']}").status_code == 404

    client.app.dependency_overrides.pop(get_current_user)
    assert client.get("/api/budgets").status_code == 401
