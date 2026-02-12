def drop_last_n_lines(filename, n):
    lines: list[str] = []

    with open(filename, "r") as f:
        lines = f.readlines()

    with open(filename, "w") as f:
        f.writelines(lines[:-n])
