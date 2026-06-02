"""Table assignment engine for Longest Table.

Strategy:
1. Identify captain parties -> seed tables (one per table).
2. Identify singles (parties of size 1) -> cluster them into dedicated
   "singles tables" with target_singles_per_table each, so singles have
   each other to talk to.
3. Place remaining groups across all tables, preferring non-singles
   tables first, spreading evenly.

Hard rules: capacity, keep-groups-together, singles cluster (0 or >=3 per table).
Soft: even spread.
"""
from math import ceil
from collections import defaultdict


def assign_tables(participants, rules, desired_table_count=0):
    seats = rules.get("seats_per_table", 14)
    min_singles = rules.get("min_singles_per_table", 3) or 3
    min_children = rules.get("min_children_per_table", 2) or 0
    min_teens = rules.get("min_teens_per_table", 2) or 0
    spread_seniors = rules.get("spread_seniors", True)
    need_captain = rules.get("one_captain_per_table", True)
    split_oversize = rules.get("split_oversize_groups", True)

    warnings = []
    total_people = len(participants)

    # ---- Group participants by order_id into parties
    groups = defaultdict(list)
    for p in participants:
        groups[p["order_id"]].append(p)

    parties = []
    for order_id, people in groups.items():
        people_sorted = sorted(people, key=lambda p: (0 if p["is_captain"] else 1))
        if len(people_sorted) > seats:
            if split_oversize:
                warnings.append(
                    f"STRONG WARNING: Order #{order_id} has {len(people_sorted)} people; "
                    f"splitting across multiple tables (chunks of {seats})."
                )
                for i in range(0, len(people_sorted), seats):
                    chunk = people_sorted[i:i + seats]
                    parties.append({
                        "order_id": order_id,
                        "people": chunk,
                        "has_captain": any(p["is_captain"] for p in chunk),
                        "is_split": True,
                    })
            else:
                warnings.append(
                    f"STRONG WARNING: Order #{order_id} has {len(people_sorted)} people "
                    f"(> {seats} seats) and split is disabled."
                )
                parties.append({
                    "order_id": order_id,
                    "people": people_sorted,
                    "has_captain": any(p["is_captain"] for p in people_sorted),
                    "is_split": False,
                })
        else:
            parties.append({
                "order_id": order_id,
                "people": people_sorted,
                "has_captain": any(p["is_captain"] for p in people_sorted),
                "is_split": False,
            })

    # ---- Determine table count
    min_tables_by_capacity = ceil(total_people / seats) if seats > 0 else 1
    captain_parties = [p for p in parties if p["has_captain"]]
    num_captains = len(captain_parties)

    if desired_table_count and desired_table_count > 0:
        n_tables = max(desired_table_count, min_tables_by_capacity, 1)
        if desired_table_count < min_tables_by_capacity:
            warnings.append(
                f"STRONG WARNING: {total_people} people need at least {min_tables_by_capacity} tables "
                f"at {seats} seats each. Requested {desired_table_count}, so using {n_tables}."
            )
    else:
        n_tables = max(min_tables_by_capacity, 1)

    if need_captain and num_captains < n_tables:
        warnings.append(
            f"STRONG WARNING: Only {num_captains} table captain(s) for {n_tables} tables. "
            f"{n_tables - num_captains} table(s) will have no captain."
        )
    if need_captain and num_captains > n_tables:
        warnings.append(
            f"Note: {num_captains} captains but only {n_tables} tables. "
            f"Some captains will share a table."
        )

    # ---- Initialize tables
    tables = [{
        "number": i + 1,
        "captain": None,
        "parties": [],
        "people": [],
        "is_singles_table": False,
    } for i in range(n_tables)]

    def fill(t):
        return len(t["people"])

    def can_fit(t, party):
        return fill(t) + len(party["people"]) <= seats

    def place(t, party):
        t["parties"].append(party)
        t["people"].extend(party["people"])
        if not t["captain"]:
            for p in party["people"]:
                if p["is_captain"]:
                    t["captain"] = p
                    break

    # ---- Step 1: seed tables with captain parties
    captain_parties.sort(key=lambda p: -len(p["people"]))
    extra_captain_parties = []
    for i, cp in enumerate(captain_parties):
        if i < n_tables:
            place(tables[i], cp)
        else:
            extra_captain_parties.append(cp)

    # ---- Step 2: identify singles and designate singles tables
    other_parties = [p for p in parties if not p["has_captain"]] + extra_captain_parties
    singles = [p for p in other_parties if len(p["people"]) == 1]
    groups_list = [p for p in other_parties if len(p["people"]) > 1]

    num_singles = len(singles)
    if num_singles > 0:
        # Use floor division so every singles table is GUARANTEED >= min_singles
        # (remainder spread across the tables so some may have min_singles+1)
        if num_singles < min_singles:
            # Not enough singles to form even one valid singles table
            num_singles_tables = 1
            warnings.append(
                f"STRONG WARNING: Only {num_singles} single(s) total, but rule "
                f"requires at least {min_singles} per singles table. "
                f"The singles table will have fewer than {min_singles} singles."
            )
        else:
            num_singles_tables = num_singles // min_singles
        num_singles_tables = min(num_singles_tables, n_tables)
        # Pick the tables with the smallest captain party so far (most room)
        candidate_idx = sorted(range(n_tables), key=lambda i: fill(tables[i]))
        for i in candidate_idx[:num_singles_tables]:
            tables[i]["is_singles_table"] = True

    singles_tables = [t for t in tables if t["is_singles_table"]]
    non_singles_tables = [t for t in tables if not t["is_singles_table"]]

    unplaced = []

    # ---- Step 3: distribute singles across singles tables
    def singles_count(t):
        return sum(1 for party in t["parties"] if len(party["people"]) == 1)

    for s in singles:
        candidates = []
        for t in singles_tables:
            if not can_fit(t, s):
                continue
            candidates.append((singles_count(t), fill(t), t["number"], t))
        if not candidates:
            # Overflow: put in any table with room (will trigger warning)
            for t in tables:
                if can_fit(t, s):
                    candidates.append((99, fill(t), t["number"], t))
        if not candidates:
            unplaced.append(s)
            continue
        candidates.sort(key=lambda x: (x[0], x[1], x[2]))
        place(candidates[0][3], s)

    # ---- Step 4: place groups - prefer non-singles tables first, spread evenly,
    # cluster children/teens, spread seniors.
    def count_age(t, label):
        return sum(1 for p in t["people"] if (p.get("age_range") or "").lower() == label)

    def party_age_counts(party):
        c = t_ = s_ = 0
        for p in party["people"]:
            a = (p.get("age_range") or "").lower()
            if a == "child":
                c += 1
            elif a == "teen":
                t_ += 1
            elif a == "senior":
                s_ += 1
        return c, t_, s_

    def age_score(t, party):
        pc, pt, ps = party_age_counts(party)
        if pc == 0 and pt == 0 and ps == 0:
            return 0
        tc = count_age(t, "child")
        tt = count_age(t, "teen")
        ts = count_age(t, "senior")
        score = 0
        # Children: strongly prefer joining a table that already has children.
        # Big bonus if this would bring the table from <min to >=min.
        if pc > 0:
            if tc > 0:
                score -= 8  # any child already there is a strong magnet
            score -= 2 * tc
            if tc + pc >= min_children and tc < min_children:
                score -= 5  # crossing the threshold is extra valuable
        # Teens: same treatment.
        if pt > 0:
            if tt > 0:
                score -= 8
            score -= 2 * tt
            if tt + pt >= min_teens and tt < min_teens:
                score -= 5
        # Seniors: spread out.
        if ps > 0 and spread_seniors:
            score += 2 * ts
        return score

    # Sort groups: families with children/teens first (so later families cluster
    # onto them), then by size desc.
    def group_sort_key(party):
        pc, pt, _ = party_age_counts(party)
        has_kids = 1 if (pc + pt) > 0 else 0
        return (-has_kids, -len(party["people"]))
    groups_list.sort(key=group_sort_key)
    for party in groups_list:
        def score_table(t):
            return (fill(t) + age_score(t, party), t["number"])
        candidates = [
            (score_table(t), t) for t in non_singles_tables if can_fit(t, party)
        ]
        if not candidates:
            candidates = [
                (score_table(t), t) for t in singles_tables if can_fit(t, party)
            ]
        if not candidates:
            unplaced.append(party)
            continue
        candidates.sort(key=lambda x: x[0])
        place(candidates[0][1], party)

    # ---- Validation & warnings
    if unplaced:
        n_un = sum(len(p["people"]) for p in unplaced)
        warnings.append(
            f"STRONG WARNING: {n_un} people in {len(unplaced)} group(s) could not be "
            f"placed. Add more tables or increase seats."
        )

    if need_captain:
        for t in tables:
            if fill(t) > 0 and not t["captain"]:
                warnings.append(
                    f"STRONG WARNING: Table {t['number']} has no captain."
                )

    for t in tables:
        sc = singles_count(t)
        if 0 < sc < min_singles:
            warnings.append(
                f"STRONG WARNING: Table {t['number']} has only {sc} single(s) "
                f"(rule: 0 or at least {min_singles})."
            )

    # Age-range soft checks — only warn when clustering was actually possible.
    # If only one table has a lone child/teen, their family is a one-child
    # family and there's nothing to consolidate. We warn only when 2+ tables
    # each have fewer than the minimum — those could have been merged.
    def tables_below(label, minimum):
        return [t for t in tables if 0 < count_age(t, label) < minimum]

    if min_children > 0:
        low = tables_below("child", min_children)
        if len(low) >= 2:
            nums = ", ".join(str(t["number"]) for t in low)
            warnings.append(
                f"Tables {nums} each have fewer than {min_children} children — "
                f"children could have been clustered together."
            )
    if min_teens > 0:
        low = tables_below("teen", min_teens)
        if len(low) >= 2:
            nums = ", ".join(str(t["number"]) for t in low)
            warnings.append(
                f"Tables {nums} each have fewer than {min_teens} teens — "
                f"teens could have been clustered together."
            )
    if spread_seniors:
        senior_counts = [count_age(t, "senior") for t in tables if fill(t) > 0]
        if senior_counts:
            max_s = max(senior_counts)
            avg_s = sum(senior_counts) / len(senior_counts)
            for t in tables:
                sc_s = count_age(t, "senior")
                if sc_s >= 4 and sc_s >= max_s and avg_s < sc_s - 1:
                    warnings.append(
                        f"Table {t['number']} has {sc_s} seniors clustered "
                        f"(avg is {avg_s:.1f})."
                    )

    return {
        "tables": tables,
        "warnings": warnings,
        "unplaced": [p for party in unplaced for p in party["people"]],
        "summary": {
            "total_people": total_people,
            "num_tables": n_tables,
            "num_singles_tables": sum(1 for t in tables if t["is_singles_table"]),
            "num_singles": num_singles,
            "seats_per_table": seats,
            "total_capacity": n_tables * seats,
            "captains_available": num_captains,
        },
    }
