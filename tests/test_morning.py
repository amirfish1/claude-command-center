import unittest


class TestInfraSmoke(unittest.TestCase):
    """Sanity check: unittest discovery works from the repo root."""

    def test_one_plus_one(self):
        self.assertEqual(1 + 1, 2)


class TestGetMorningState(unittest.TestCase):
    def test_returns_expected_top_level_keys(self):
        from morning import get_morning_state
        state = get_morning_state()
        self.assertIn("goals", state)
        self.assertIn("strategic", state)
        self.assertIn("tactical", state)
        self.assertIn("inbox", state)
        self.assertIn("last_refreshed", state)

    def test_goals_have_required_fields(self):
        from morning import get_morning_state
        state = get_morning_state()
        self.assertGreaterEqual(len(state["goals"]), 1)
        goal = state["goals"][0]
        for field in ("slug", "name", "life_area", "ribbon"):
            self.assertIn(field, goal, f"goal missing '{field}': {goal}")

    def test_tactical_items_reference_goal_slugs(self):
        from morning import get_morning_state
        state = get_morning_state()
        goal_slugs = {g["slug"] for g in state["goals"]}
        for t in state["tactical"]:
            if t.get("goal_slug") is not None:
                self.assertIn(t["goal_slug"], goal_slugs,
                              f"tactical item references unknown goal: {t}")


class TestGetGoalDetail(unittest.TestCase):
    def test_returns_none_for_unknown_slug(self):
        from morning import get_goal_detail
        self.assertIsNone(get_goal_detail("does-not-exist"))

    def test_returns_expected_shape_for_known_slug(self):
        from morning import get_goal_detail
        detail = get_goal_detail("bym-growth")
        self.assertIsNotNone(detail)
        for key in ("slug", "name", "life_area", "intent_markdown",
                    "strategies", "tactical_tagged", "deliverables",
                    "context_library", "recent_sessions"):
            self.assertIn(key, detail, f"missing key {key!r}")

    def test_strategies_have_session_state(self):
        from morning import get_goal_detail
        detail = get_goal_detail("bym-growth")
        self.assertGreaterEqual(len(detail["strategies"]), 1)
        for s in detail["strategies"]:
            self.assertIn("id", s)
            self.assertIn("text", s)
            self.assertIn("status", s)
            self.assertIn("session_state", s)
            self.assertIn(s["session_state"], ("alive", "dormant", "never", "dropped"))


if __name__ == "__main__":
    unittest.main()
