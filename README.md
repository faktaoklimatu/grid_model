# grid-model

## Study: Power generation in Czechia without coal

Welcome to the repository of source code for the model used in the publication
https://faktaoklimatu.cz/studie/2024-vyroba-elektriny-v-cesku-bez-uhli.

The relevant source code and outputs will be published here shortly.

## Solvers

- The default open-source solver `CBC` comes with the library `PuLP`. However, this solver is very slow.
- A slightly faster option is the open-source solver [HiGHS](https://highs.dev/) that has the python library installed through `requirements.txt`. For the solver to be available, one needs to install the executable.
  - For Fedora Linux, [an unofficial Copr repository](https://copr.fedorainfracloud.org/coprs/mgrabovs/HiGHS/) is available.
  - For other Linux distributions, [building from the source code](https://ergo-code.github.io/HiGHS/dev/interfaces/cpp/) might be required.
  - For Mac OS, `brew install highs` does the trick.
- Solid fast for our model is the commercial solver [Mosek](https://www.mosek.com/). This can be easily installed by `pip install Mosek` but one also needs to obtain a license and store it to `~/mosek/mosek.lic`. 30 days free trial license can be easily obtained at [https://www.mosek.com/try/](https://www.mosek.com/try/).

