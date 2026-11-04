"""Rerun commands for a run's failing tests (qasight/services/rerun_commands.py)."""

from __future__ import annotations

import pytest

from qasight.services.rerun_commands import (
    MAX_TESTS,
    FailingCase,
    pytest_node_id,
    rerun_commands,
)


@pytest.mark.parametrize(
    ("full_name", "name", "expected"),
    [
        # Allure pytest: module#function, module.Class#method.
        (
            "tests.api.test_orders#test_create",
            "test_create",
            "tests/api/test_orders.py::test_create",
        ),
        (
            "tests.api.test_orders.TestCancel#test_twice",
            "test_twice",
            "tests/api/test_orders.py::TestCancel::test_twice",
        ),
        # JUnit from pytest --junitxml: classname.name.
        ("tests.ui.test_login.test_form", "test_form", "tests/ui/test_login.py::test_form"),
        # Already a node id.
        ("tests/test_a.py::test_x[1]", "test_x[1]", "tests/test_a.py::test_x[1]"),
        # Not a Python test module.
        ("LoginSpec.rejects a wrong password", "rejects a wrong password", None),
    ],
)
def test_pytest_node_id(full_name: str, name: str, expected: str | None) -> None:
    assert pytest_node_id(full_name, name) == expected


def test_one_command_per_runner() -> None:
    cases = [
        FailingCase("test_create", "tests.api.test_orders#test_create", "", "api"),
        FailingCase("test_create", "tests.api.test_orders#test_create", "", "api"),  # retried twice
        FailingCase("checkout (total)", "cart.spec.ts › checkout (total)", "", "e2e"),
        FailingCase("Login works", "features/login.feature", "", "bdd"),
    ]
    commands = rerun_commands(cases, {"api": "pytest", "e2e": "playwright", "bdd": "behave"})

    assert commands == [
        "pytest tests/api/test_orders.py::test_create",
        "npx playwright test --grep 'checkout \\(total\\)'",
        "behave --name '^Login works$'",
    ]


def test_ingested_junit_gets_pytest_only_for_python_modules() -> None:
    py = FailingCase("test_form", "tests.ui.test_login.test_form", "", "junit")
    jest = FailingCase("renders", "Button renders", "", "junit")

    assert rerun_commands([py], {"junit": "junit"}) == ["pytest tests/ui/test_login.py::test_form"]
    assert rerun_commands([jest], {"junit": "junit"}) == []


def test_too_many_failures_get_no_command() -> None:
    cases = [
        FailingCase(f"test_{i}", f"tests.test_a#test_{i}", "", "unit") for i in range(MAX_TESTS + 1)
    ]
    assert rerun_commands(cases, {"unit": "pytest"}) == []


def test_runners_named_by_their_result_files() -> None:
    cases = [
        FailingCase("adds tax", "src/cart.test.js › cart › adds tax", "", "unit", "jest"),
        FailingCase("refunds", "src/pay.test.js › refunds", "", "unit", "jest"),
        FailingCase("negative", "example.com/shop.TestCart/negative", "", "go", "go"),
        FailingCase("TestLater", "example.com/shop/v2.TestLater", "", "go", "go"),
        FailingCase("Rows(2)", "Shop.Tests.RowTests.Rows(2)", "", "net", "dotnet"),
        FailingCase("Rejects", "Shop.Tests.CartTests.Rejects", "", "net", "dotnet"),
        FailingCase("Wrong Password", "Tests.Login.Wrong Password", "", "e2e", "robot"),
    ]
    commands = rerun_commands(
        cases, {"unit": "junit", "go": "junit", "net": "command", "e2e": "junit"}
    )
    assert commands == [
        "npx jest src/cart.test.js src/pay.test.js -t '^(cart adds tax|refunds)$'",
        "go test -run '^(TestCart|TestLater)$' example.com/shop example.com/shop/v2",
        "dotnet test --filter 'FullyQualifiedName=Shop.Tests.RowTests.Rows"
        "|FullyQualifiedName=Shop.Tests.CartTests.Rejects'",
        "robot --test 'Wrong Password' tests",
    ]


def test_vitest_stage() -> None:
    case = FailingCase("adds", "src/cart.test.ts › cart › adds", "", "unit", "vitest")
    assert rerun_commands([case], {"unit": "vitest"}) == [
        "npx vitest run src/cart.test.ts -t '^(cart adds)$'"
    ]


def test_java_needs_the_build_tool(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cases = [
        FailingCase("addsTax()", "com.acme.CartTest.addsTax()", "", "junit"),
        FailingCase("rejects", "com.acme.CartTest.rejects", "", "junit"),
        FailingCase("refunds", "com.acme.PayTest.refunds", "", "junit"),
    ]
    assert rerun_commands(cases, {"junit": "junit"}) == []
    assert rerun_commands(cases, {"junit": "junit"}, repo=tmp_path) == []

    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert rerun_commands(cases, {"junit": "junit"}, repo=tmp_path) == [
        "mvn test '-Dtest=CartTest#addsTax+rejects,PayTest#refunds' "
        "-Dsurefire.failIfNoSpecifiedTests=false"
    ]

    (tmp_path / "pom.xml").unlink()
    (tmp_path / "build.gradle.kts").write_text("", encoding="utf-8")
    (tmp_path / "gradlew").write_text("", encoding="utf-8")
    assert rerun_commands(cases, {"junit": "junit"}, repo=tmp_path) == [
        "./gradlew test --tests com.acme.CartTest.addsTax --tests com.acme.CartTest.rejects "
        "--tests com.acme.PayTest.refunds"
    ]


def test_java_display_names_are_left_out(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    case = FailingCase("adds tax to totals", "com.acme.CartTest.adds tax to totals", "", "junit")
    assert rerun_commands([case], {"junit": "junit"}, repo=tmp_path) == []


def test_rspec(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cases = [
        FailingCase("adds tax", "spec/cart_spec.rb › Cart adds tax", "", "ruby", "rspec"),
        FailingCase("refunds", "spec/pay_spec.rb › Pay refunds", "", "ruby", "rspec"),
    ]
    assert rerun_commands(cases, {"ruby": "junit"}) == [
        "rspec spec/cart_spec.rb spec/pay_spec.rb -e 'Cart adds tax' -e 'Pay refunds'"
    ]
    (tmp_path / "Gemfile").write_text("", encoding="utf-8")
    assert rerun_commands(cases[:1], {"ruby": "junit"}, repo=tmp_path) == [
        "bundle exec rspec spec/cart_spec.rb -e 'Cart adds tax'"
    ]


def test_android_instrumentation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cases = [
        FailingCase("addsTax", "com.acme.CartTest.addsTax", "", "app", "android"),
        FailingCase("opens", "com.acme.MainTest.opens", "", "app", "android"),
    ]
    assert rerun_commands(cases, {"app": "junit"}, repo=tmp_path) == [
        "gradle connectedAndroidTest "
        "'-Pandroid.testInstrumentationRunnerArguments.class="
        "com.acme.CartTest#addsTax,com.acme.MainTest#opens'"
    ]
    (tmp_path / "gradlew").write_text("", encoding="utf-8")
    assert rerun_commands(cases[:1], {"app": "junit"}, repo=tmp_path) == [
        "./gradlew connectedAndroidTest "
        "'-Pandroid.testInstrumentationRunnerArguments.class=com.acme.CartTest#addsTax'"
    ]


def test_k6_and_xctest_get_no_command() -> None:
    cases = [
        FailingCase("status is 200", "checks › status is 200", "", "load", "k6"),
        FailingCase("testAdds()", "ShopTests/CartTests/testAdds()", "", "ios", "xctest"),
    ]
    assert rerun_commands(cases, {"load": "junit", "ios": "junit"}) == []
