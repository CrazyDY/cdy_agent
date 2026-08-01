from importlib.metadata import requires


def test_websocket_transport_is_a_runtime_dependency() -> None:
    requirements = requires("cdy-agent")

    assert requirements is not None
    runtime_requirements = [
        requirement for requirement in requirements if "extra ==" not in requirement
    ]
    assert any(
        requirement.lower().startswith("uvicorn[standard]>=0.35.0")
        for requirement in runtime_requirements
    )
