from ..parser.convert import IS_SPICE


def generate_seed_prompt(gen_count_per_session: int, circuit_requirements):

    final_seed_prompt = (
        "# Special Instructions For This Task Instance:\n"
        f"You have to generate in total of {gen_count_per_session} netlists.\n"
        "Below are the constraints for each netlist generation:\n"
    )

    for idx in range(gen_count_per_session):
        requirement = circuit_requirements[idx]
        component_subset = requirement.component_subset
        num_components = requirement.num_components

        no_specifer_str = ", and without specifiers" if IS_SPICE else ""
        component_list = ", ".join(
            (
                f"`{prefix}{' with kind keyword: ' + ', '.join(kind) if kind else ' without kind keyword'}"
                f"{', and with specifiers: ' + ', '.join(specifiers) if specifiers else no_specifer_str}`"
            )
            for prefix, kind, specifiers in component_subset
        )

        prompt_seed = (
            f"## Contraints for Netlist {idx}:\n"
            f"This is the exact number of components for this instance: "
            f"{num_components}, "
            f"you can't go over nor go under this number.\n"
            f"You can only use the following subset of components: "
            f"{component_list}, "
            f"other components are not allowed.\n"
            f"Please ensure that the generated netlist {idx} is enclosed in "
            f"<netlist{idx}> and </netlist{idx}> tags\n"
        )
        final_seed_prompt += prompt_seed + "\n"

    return final_seed_prompt
