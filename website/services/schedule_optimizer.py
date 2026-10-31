"""Pure, deterministic monthly CP-SAT model. No Django models or writes here."""
from dataclasses import dataclass, field
from collections import defaultdict
from itertools import combinations

from ortools.sat.python import cp_model


@dataclass(frozen=True)
class Duty:
    id: int
    occurrence: int
    date: str
    time: str
    team: int
    count: int
    minimum_mentors: int | None
    candidates: dict  # member ID -> mentor/trainee; already validated availability
    locked: tuple = ()


@dataclass
class Problem:
    duties: list
    previous_load: dict = field(default_factory=dict)  # unique services in previous month + published current month
    previous_mentor_load: dict = field(default_factory=dict)
    previous_team_members: dict = field(default_factory=dict)  # last previous service per team
    previous_pairs: dict = field(default_factory=dict)


def solve(problem, deterministic_budget=1.0):
    """Lexicographic objectives: fix each optimum before considering the next.

    If a phase reaches its deterministic budget, retain its best feasible result
    and stop: never trade a higher priority objective for a lower one.
    """
    duties = sorted(problem.duties, key=lambda d: (d.date, d.time, d.team, d.id))
    model = cp_model.CpModel()
    x, active, by_person_day_occurrence = {}, {}, defaultdict(list)
    for duty in duties:
        eligible = duty.candidates if duty.minimum_mentors else {}
        for member in sorted(eligible):
            x[duty.id, member] = model.new_bool_var(f'x{duty.id}_{member}')
            by_person_day_occurrence[member, duty.date, duty.occurrence].append(x[duty.id, member])
        values = [x[duty.id, member] for member in sorted(eligible)]
        active[duty.id] = model.new_bool_var(f'active{duty.id}')
        model.add(sum(values) <= duty.count * active[duty.id])
        model.add(sum(values) >= active[duty.id])
        mentors = [x[duty.id, m] for m in sorted(eligible) if eligible[m] == 'mentor']
        model.add(sum(mentors) >= (duty.minimum_mentors or 1) * active[duty.id])
        for member in duty.locked:
            if (duty.id, member) not in x:
                raise ValueError('Locked assignment is no longer eligible.')
            model.add(x[duty.id, member] == 1)

    presence, by_person_day = {}, defaultdict(list)
    for key, variables in sorted(by_person_day_occurrence.items()):
        present = model.new_bool_var(f'service{key}')
        model.add_max_equality(present, variables)
        presence[key] = present
        by_person_day[key[:2]].append(present)
    for variables in by_person_day.values():
        model.add(sum(variables) <= 1)

    def squared_loads(groups, previous, label):
        result = []
        for member, variables in sorted(groups.items()):
            base = previous.get(member, 0)
            upper = base + len(variables)
            load = model.new_int_var(base, upper, f'{label}{member}')
            square = model.new_int_var(base * base, upper * upper, f'{label}sq{member}')
            model.add(load == base + sum(variables))
            model.add_multiplication_equality(square, [load, load])
            result.append(square)
        return sum(result)

    loads, mentors, workload = defaultdict(list), defaultdict(list), defaultdict(list)
    for (member, _, _), variable in presence.items():
        loads[member].append(variable)
    for duty in duties:
        for member in sorted(duty.candidates):
            if (duty.id, member) in x:
                workload[member].append(x[duty.id, member])
                if duty.candidates[member] == 'mentor':
                    mentors[member].append(x[duty.id, member])
    consecutive, repeated_pairs, training = [], [], []
    by_team = defaultdict(list)
    for duty in duties:
        by_team[duty.team].append(duty)
        training.extend(x[duty.id, m] for m, level in duty.candidates.items()
                        if level == 'trainee' and (duty.id, m) in x)

    def conjunction(variables, name):
        value = model.new_bool_var(name)
        model.add_min_equality(value, variables)
        return value

    for team, rows in sorted(by_team.items()):
        # Several teams can serve the same cult. One team has one plan per cult.
        for index, duty in enumerate(rows):
            current = {m: x[duty.id, m] for m in sorted(duty.candidates) if (duty.id, m) in x}
            if index:
                prior = rows[index - 1]
                previous = {m: x[prior.id, m] for m in sorted(prior.candidates) if (prior.id, m) in x}
                common = sorted(current.keys() & previous.keys())
                consecutive.extend(conjunction([current[m], previous[m]], f'consecutive{duty.id}_{m}') for m in common)
                if duty.count == prior.count == 2:
                    repeated_pairs.extend(conjunction([current[a], current[b], previous[a], previous[b]], f'pair{duty.id}_{a}_{b}')
                                          for a, b in combinations(common, 2))
            else:
                consecutive.extend(current[m] for m in sorted(problem.previous_team_members.get(team, ())) if m in current)
                if duty.count == 2:
                    repeated_pairs.extend(conjunction([current[a], current[b]], f'boundarypair{duty.id}_{a}_{b}')
                                          for a, b in sorted(problem.previous_pairs.get(team, ())) if a in current and b in current)

    phases = [
        ('filled_slots', sum(x.values()), True),
        ('overall_squared_load', squared_loads(loads, problem.previous_load, 'load'), False),
        ('mentor_squared_load', squared_loads(mentors, problem.previous_mentor_load, 'mentor'), False),
        ('consecutive_team_services', sum(consecutive), False),
        ('repeated_consecutive_pairs', sum(repeated_pairs), False),
        ('training_slots', sum(training), True),
        ('cross_team_squared_workload', squared_loads(workload, problem.previous_load, 'work'), False),
    ]
    report, solution = [], None
    for name, expression, maximize in phases:
        if maximize:
            model.maximize(expression)
        else:
            model.minimize(expression)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.max_deterministic_time = deterministic_budget
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if solution is None:
                raise ValueError('Não foi encontrada uma solução no limite de resolução. O rascunho foi preservado.')
            report.append({'objective': name, 'status': solver.status_name(status), 'value': None})
            break
        solution = {d.id: tuple(m for m in sorted(d.candidates) if (d.id, m) in x and solver.value(x[d.id, m])) for d in duties}
        value = int(solver.value(expression))
        report.append({'objective': name, 'status': solver.status_name(status), 'value': value})
        if status != cp_model.OPTIMAL:
            break
        model.add(expression == value)
        # Stable hints and sorted construction also make repeated identical input repeatable.
        model.clear_hints()
        for variable in x.values():
            model.add_hint(variable, solver.value(variable))
    return solution or {d.id: () for d in duties}, report
