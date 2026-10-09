from .sampling import CircuitRequirements


def generate_seed_prompt(gen_count_per_session: int, circuit_requirements: list[CircuitRequirements]) -> str:
    """The first user message of a session: per netlist, the exact component
    count, the component types it must use, those it may use, and nothing
    else. Types are worded by ComponentSample.describe(), like the
    validation errors."""
    final_seed_prompt = (
        "# Special Instructions For This Task Instance:\n"
        f"You have to generate {gen_count_per_session} netlist(s) in total.\n"
        "Below are the constraints for each netlist generation:\n"
    )

    for idx in range(gen_count_per_session):
        requirement = circuit_requirements[idx]
        required_keys = {sample.key() for sample in requirement.required}
        optional = [sample for sample in requirement.allowed if sample.key() not in required_keys]

        lines = [
            f"## Constraints for Netlist {idx}:",
            f"This is the exact number of components for this instance: {requirement.num_components}, "
            f"you can't go over nor go under this number. Only the element lines outside "
            f".subckt bodies count (an X instance counts once; directives, K lines and the "
            f"elements inside a .subckt ... .ends block do not).",
        ]
        if requirement.required:
            lines.append(
                "You MUST use each of the following components at least once, at the top level of the "
                "netlist (not only inside a .subckt body). Each line is its own component type: the same "
                "letter with another model TYPE, or with or without a kind keyword, does not count for it."
            )
            lines.extend(f"- {sample.describe()}" for sample in requirement.required)
        if optional:
            lines.append("You MAY also use the following components:")
            lines.extend(f"- {sample.describe()}" for sample in optional)
        lines.append("No other components are allowed.")
        lines.append(
            f"Please ensure that the generated netlist {idx} is enclosed in "
            f"<netlist{idx}> and </netlist{idx}> tags"
        )
        final_seed_prompt += "\n".join(lines) + "\n\n"

    return final_seed_prompt
