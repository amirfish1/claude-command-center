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


if __name__ == "__main__":
    unittest.main()
