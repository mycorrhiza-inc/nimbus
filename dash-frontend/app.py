# Import packages
import dash
from dash import Dash, html, dcc, callback, Output, Input
import pandas as pd
import plotly.express as px
from pathlib import Path
import os

DEFAULT_SCENARIO_FOLDER = os.getenv(
    "DEFAULT_SCENARIO_FOLDER",
    "/home/brad/Documents/energy-democracy/gridmodel/data/switch/scc7a_60_fuel",
)


def build_figure(dir_scenario: Path):
    dir_output = dir_scenario / "outputs"
    dir_input = dir_scenario / "inputs"
    file_buildgen = dir_output / "BuildGen.csv"
    file_geninfo = dir_input / "gen_info.csv"

    buildgen = pd.read_csv(file_buildgen, dtype={})
    buildgen.columns = ["GENERATION_PROJECT", "INVESTMENT_PERIOD", "BuildGen"]
    buildgen["GENERATION_PROJECT"] = buildgen["GENERATION_PROJECT"].str.strip()

    geninfo = pd.read_csv(file_geninfo)
    gen_tech = geninfo[["GENERATION_PROJECT", "gen_tech"]]
    gen_tech["GENERATION_PROJECT"] = gen_tech["GENERATION_PROJECT"].str.strip()

    merged_data = buildgen.merge(gen_tech, on="GENERATION_PROJECT", how="left")

    # Generate figure
    fig = px.bar(merged_data, y="BuildGen", x="INVESTMENT_PERIOD", color="gen_tech")
    return fig


def create_layout(directory_name):
    fig = build_figure(Path(DEFAULT_SCENARIO_FOLDER) / directory_name)

    return html.Div(
        [
            html.H1(f"Build Capacity for {directory_name}"),
            html.Hr(),
            dcc.Graph(figure=fig, id="graph"),
        ]
    )


app = Dash(__name__, use_pages=True, pages_folder="")
# Dynamically generate pages for each directory in DEFAULT_SCENARIO_FOLDER
homepage_list = [
    html.H2("An App for easily running economic models."),
    html.Div(dcc.Link("API Schema", href="/schema/swagger")),
    html.H3("List of graphs for generated scenarios:"),
]
scenario_path = Path(DEFAULT_SCENARIO_FOLDER)
print(os.listdir(scenario_path))
for directory in scenario_path.iterdir():
    if directory.is_dir():
        print(f"Registering page: {directory.name}")
        dash.register_page(
            directory.name,
            path=f"/{directory.name}",
            layout=create_layout(directory.name),
        )
        homepage_list.append(
            html.Div(dcc.Link(f"{directory.name}", href=f"/{directory.name}"))
        )

homepage_html = html.Div(homepage_list)
dash.register_page("home", path="/", layout=homepage_html)

app.layout = html.Div(
    [
        dash.page_container,
    ]
)

if __name__ == "__main__":
    app.run_server(debug=True, host="0.0.0.0", port=9000)
