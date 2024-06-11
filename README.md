# grid-model

## Study: Ways to clean and affordable electricity in 2050

Welcome to the repository of source code for the model used in the publication
https://faktaoklimatu.cz/studie/2024-cesty-k-ciste-a-levne-elektrine-2050.

Relevant SVG files and summary CSV files are in the folder `publication`.
Raw CSV output data is on Google Drive:
https://drive.google.com/drive/folders/1DqkC8tUl0DVa_GXwE9dhbjdMqhPspe9f?usp=sharing
 - data for CZ for the reference scenario,
 - all CSV files for the 20 scenarios in the reference set (in a ZIP file, including the file above).

## Solvers

- The default open-source solver `CBC` comes with the library `PuLP`. However, this solver is very slow.
- A slightly faster option is the open-source solver [HiGHS](https://highs.dev/) that has the python library installed through `requirements.txt`. For the solver to be available, one needs to install the executable.
  - For Fedora Linux, [an unofficial Copr repository](https://copr.fedorainfracloud.org/coprs/mgrabovs/HiGHS/) is available.
  - For other Linux distributions, [building from the source code](https://ergo-code.github.io/HiGHS/dev/interfaces/cpp/) might be required.
  - For Mac OS, `brew install highs` does the trick.
- Solid fast for our model is the commercial solver [Mosek](https://www.mosek.com/). This can be easily installed by
`pip install Mosek` but one also needs to obtain a license and store it to `~/mosek/mosek.lic`. 30 days free trial license can be easily obtained at [https://www.mosek.com/try/](https://www.mosek.com/try/).
