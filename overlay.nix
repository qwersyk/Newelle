(self0: super0:
  let
    myOverride = {
      packageOverrides = self: super: {

        "pip-install-test" = super.buildPythonPackage rec {
          pname = "pip-install-test";
          version = "0.5";
          src = super0.fetchurl {
            url = "https://files.pythonhosted.org/packages/15/8e/4fbc92846184e1080af77da38d55928a5486e0bc5e2ec8342c7db378d7f1/pip_install_test-0.5-py3-none-any.whl";
            sha256 =
              "623887f5ce0b4695ec3c0503aa4f394253a403e2bb952417b3a778f0802dbe0b";
          };
          format = "wheel";
          doCheck = false;
          buildInputs = [ ];
          checkInputs = [ ];
          nativeBuildInputs = [ ];
          propagatedBuildInputs = [
          ];
        };

        "newspaper3k" = super.buildPythonPackage rec {
          pname = "newspaper3k";
          version = "0.2.8";
          src = super0.fetchurl {
            url =
              "https://files.pythonhosted.org/packages/d7/b9/51afecb35bb61b188a4b44868001de348a0e8134b4dfa00ffc191567c4b9/newspaper3k-0.2.8-py3-none-any.whl";
            sha256 =
              "44a864222633d3081113d1030615991c3dbba87239f6bbf59d91240f71a22e3e";
          };
          format = "wheel";
          doCheck = false;
          buildInputs = [ ];
          checkInputs = [ ];
          nativeBuildInputs = [ ];
          propagatedBuildInputs = [
            super.feedparser
            super.tldextract
          ];
        };

      };
    };
  in {
    python3 = super0.python3.override myOverride;
  }
)
