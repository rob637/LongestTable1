import unittest

from assigner import assign_tables


def make_party(order_id, size, captain=False):
    people = []
    for index in range(size):
        people.append({
            "id": f"{order_id}-{index}",
            "order_id": order_id,
            "is_captain": 1 if captain and index == 0 else 0,
            "age_range": "Adult",
        })
    return people


class AssignTablesTests(unittest.TestCase):
    def test_auto_table_count_uses_capacity_not_captain_count(self):
        participants = []
        for order_id in range(6):
            participants.extend(make_party(f"capt-{order_id}", 2, captain=True))

        result = assign_tables(participants, {"seats_per_table": 4, "one_captain_per_table": True})

        self.assertEqual(result["summary"]["num_tables"], 3)
        self.assertEqual(sorted(len(t["people"]) for t in result["tables"]), [4, 4, 4])

    def test_manual_table_count_can_request_more_tables(self):
        participants = []
        for order_id in range(6):
            participants.extend(make_party(f"capt-{order_id}", 2, captain=True))

        result = assign_tables(
            participants,
            {"seats_per_table": 4, "one_captain_per_table": True},
            desired_table_count=6,
        )

        self.assertEqual(result["summary"]["num_tables"], 6)
        self.assertEqual(sorted(len(t["people"]) for t in result["tables"]), [2, 2, 2, 2, 2, 2])

    def test_manual_table_count_below_capacity_uses_minimum_feasible(self):
        participants = []
        for order_id in range(6):
            participants.extend(make_party(f"capt-{order_id}", 2, captain=True))

        result = assign_tables(
            participants,
            {"seats_per_table": 4, "one_captain_per_table": True},
            desired_table_count=2,
        )

        self.assertEqual(result["summary"]["num_tables"], 3)
        self.assertTrue(any("Requested 2" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()