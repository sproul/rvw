"""Running the pure Hammerspoon lua modules through the hs command line tool.

There is no standalone lua interpreter on these machines: Hammerspoon embeds its
own, and `hs -c` evaluates a fragment inside the running Hammerspoon. That is
only safe for a module that touches nothing when it loads, which is why the
menu bar's decisions live in `hammerspoon/rvw_state.lua` with no hs dependency
at all. Everything that does call hs (the menu bar itself, the alerts, the
hotkey bindings) is left to manual verification.

Without a running Hammerspoon these tests skip rather than fail: the assistant
does not need one to work, and a machine without it must still be able to run
the suite.
"""

import subprocess
import unittest
from pathlib import Path

repo_dir = Path(__file__).resolve().parents[1]
hammerspoon_dir = repo_dir / "hammerspoon"
hs_command = "hs"
evaluation_timeout_seconds = 20


def hs_is_reachable():
    """Whether a running Hammerspoon will evaluate lua for us."""
    try:
        finished = subprocess.run([hs_command, "-c", "return 1"], capture_output=True,
                                  text=True, timeout=evaluation_timeout_seconds)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return finished.returncode == 0 and finished.stdout.strip() == "1"


def evaluate_lua(body):
    """Evaluate one lua fragment in Hammerspoon and return what it printed."""
    finished = subprocess.run([hs_command, "-c", body], capture_output=True, text=True,
                              timeout=evaluation_timeout_seconds)
    if finished.returncode != 0:
        raise AssertionError("FAIL hs refused %r: %s" % (body, finished.stderr.strip()))
    return finished.stdout.strip()


def load_module_expression(module_name):
    """A lua expression that loads one pure module from the checkout."""
    return 'dofile("%s/%s.lua")' % (hammerspoon_dir, module_name)


# The running Hammerspoon has its own copy of every rvw module from whenever its
# configuration was last loaded, and require() would hand a test that copy rather
# than the file it is testing. Forgetting them all first is what makes a test
# measure the checkout. The live menu bar keeps working: it holds references to
# the tables it was given, not to this cache.
forget_loaded_modules = '''
for name in pairs(package.loaded) do
  if name:match("^rvw_") then package.loaded[name] = nil end
end
'''


class PureLuaTestCase(unittest.TestCase):
    """Base class for tests that need Hammerspoon's lua and nothing else."""

    module_name = "rvw_state"

    @classmethod
    def setUpClass(cls):
        if not hs_is_reachable():
            raise unittest.SkipTest("Hammerspoon is not running, so its lua cannot be tested")

    def evaluate(self, expression):
        """The value of one lua expression, with the module under test as `module`."""
        return self.evaluate_body("return %s" % expression)

    def evaluate_body(self, body):
        """The value returned by several lines of lua, for what an expression cannot say."""
        return evaluate_lua("%s\nlocal module = %s\n%s"
                           % (forget_loaded_modules,
                              load_module_expression(self.module_name), body))
