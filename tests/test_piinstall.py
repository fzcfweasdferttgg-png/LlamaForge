import json
import os
import shutil
import tempfile
import threading
import unittest

import conftest_paths  # noqa: F401
import pirun
import piinstall


def fake_which(table):
    return lambda name: table.get(name)


class Runner:
    """Stands in for npm: records the command and lays the package down."""

    def __init__(self, version="0.84.0", rc=0, out="", lay=True):
        self.calls, self.version, self.rc, self.out, self.lay = [], version, rc, out, lay

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append({"cmd": cmd, "env": env, "timeout": timeout})
        if cmd[1:] == ["--version"]:
            return 0, "v24.14.0\n"
        if self.lay:
            prefix = cmd[cmd.index("--prefix") + 1]
            pkg = os.path.join(prefix, "node_modules", *pirun.PACKAGE)
            os.makedirs(os.path.join(pkg, "dist"), exist_ok=True)
            with open(os.path.join(pkg, "package.json"), "w") as f:
                json.dump({"name": "/".join(pirun.PACKAGE), "version": self.version,
                           "bin": {"pi": "dist/cli.js"}}, f)
            open(os.path.join(pkg, "dist", "cli.js"), "w").close()
        return self.rc, self.out


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.addCleanup(setattr, pirun, "MANAGED_DIR", pirun.MANAGED_DIR)
        pirun.MANAGED_DIR = os.path.join(self.d, "agents", "pi")
        # a node install laid out like the Windows zip: npm beside node
        self.nodedir = os.path.join(self.d, "node")
        self.npm = os.path.join(self.nodedir, "node_modules", "npm", "bin", "npm-cli.js")
        os.makedirs(os.path.dirname(self.npm))
        open(self.npm, "w").close()
        self.node = os.path.join(self.nodedir, "node.exe")
        open(self.node, "w").close()


class NodeVersion(unittest.TestCase):
    def test_parses_and_compares(self):
        self.assertEqual(piinstall.parse_version("v24.14.0\n"), (24, 14, 0))
        self.assertEqual(piinstall.parse_version("v22.19.0"), (22, 19, 0))
        self.assertIsNone(piinstall.parse_version("command not found"))
        self.assertTrue(piinstall.new_enough((22, 19, 0)))
        self.assertTrue(piinstall.new_enough((23, 0, 0)))
        self.assertFalse(piinstall.new_enough((22, 18, 9)))
        self.assertFalse(piinstall.new_enough(None))


class NodeInfo(Base):
    def test_reports_path_version_and_ok(self):
        info = piinstall.node_info(which=fake_which({"node": self.node}), run=Runner())
        self.assertEqual(info, {"path": self.node, "version": "24.14.0", "ok": True})

    def test_too_old(self):
        old = lambda cmd, env=None, timeout=None: (0, "v20.11.1\n")
        info = piinstall.node_info(which=fake_which({"node": self.node}), run=old)
        self.assertEqual((info["version"], info["ok"]), ("20.11.1", False))

    def test_missing(self):
        self.assertEqual(piinstall.node_info(which=fake_which({}), run=Runner()),
                         {"path": "", "version": "", "ok": False})


class NpmCli(Base):
    def test_beside_node_on_windows(self):
        self.assertEqual(piinstall.npm_cli(self.node, which=fake_which({})), self.npm)

    def test_posix_prefix_layout(self):
        prefix = os.path.join(self.d, "usr")
        node = os.path.join(prefix, "bin", "node")
        npm = os.path.join(prefix, "lib", "node_modules", "npm", "bin", "npm-cli.js")
        os.makedirs(os.path.dirname(node))
        os.makedirs(os.path.dirname(npm))
        open(node, "w").close()
        open(npm, "w").close()
        self.assertEqual(piinstall.npm_cli(node, which=fake_which({})), npm)

    def test_falls_back_to_the_npm_shim_on_path(self):
        other = os.path.join(self.d, "elsewhere")
        os.makedirs(other)
        node = os.path.join(other, "node.exe")
        open(node, "w").close()
        shim = os.path.join(self.nodedir, "npm.cmd")
        open(shim, "w").close()
        self.assertEqual(piinstall.npm_cli(node, which=fake_which({"npm": shim})), self.npm)

    def test_none_when_npm_is_nowhere(self):
        node = os.path.join(self.d, "lonely-node")
        open(node, "w").close()
        self.assertIsNone(piinstall.npm_cli(node, which=fake_which({})))


class Install(Base):
    def which(self):
        return fake_which({"node": self.node})

    def test_installs_into_the_managed_prefix_never_global(self):
        run = Runner()
        ok, log = piinstall.install(which=self.which(), run=run)
        self.assertTrue(ok, log)
        cmd = run.calls[-1]["cmd"]
        self.assertEqual(cmd[:3], [self.node, self.npm, "install"])
        self.assertEqual(cmd[cmd.index("--prefix") + 1], pirun.MANAGED_DIR)
        self.assertIn("@earendil-works/pi-coding-agent@latest", cmd)
        self.assertIn("--ignore-scripts", cmd)
        self.assertNotIn("-g", cmd)
        self.assertNotIn("--global", cmd)
        self.assertTrue(run.calls[-1]["env"]["PATH"].startswith(self.nodedir + os.pathsep))
        self.assertEqual(piinstall.installed(), "0.84.0")
        self.assertIn("0.84.0", log)

    def test_refuses_without_node(self):
        run = Runner()
        ok, log = piinstall.install(which=fake_which({}), run=run)
        self.assertFalse(ok)
        self.assertIn("Node.js", log)
        self.assertEqual(run.calls, [])

    def test_refuses_old_node(self):
        old = lambda cmd, env=None, timeout=None: (0, "v20.11.1\n")
        ok, log = piinstall.install(which=self.which(), run=old)
        self.assertFalse(ok)
        self.assertIn("22.19", log)

    def test_npm_failure_is_reported(self):
        ok, log = piinstall.install(which=self.which(), run=Runner(rc=1, out="E404 not found", lay=False))
        self.assertFalse(ok)
        self.assertIn("E404", log)

    def test_exit_zero_without_a_package_is_a_failure(self):
        ok, log = piinstall.install(which=self.which(), run=Runner(lay=False))
        self.assertFalse(ok)
        self.assertEqual(piinstall.installed(), "")

    def test_one_install_at_a_time(self):
        gate, entered = threading.Event(), threading.Event()
        inner = Runner()

        def slow(cmd, env=None, timeout=None):
            if "install" in cmd:
                entered.set()
                gate.wait(5)
            return inner(cmd, env, timeout)

        t = threading.Thread(target=piinstall.install, kwargs={"which": self.which(), "run": slow})
        t.start()
        self.assertTrue(entered.wait(5))
        try:
            self.assertTrue(piinstall.busy())
            ok, log = piinstall.install(which=self.which(), run=Runner())
            self.assertFalse(ok)
            self.assertIs(log, piinstall.BUSY)
            self.assertEqual(piinstall.remove(), (False, piinstall.BUSY))
        finally:
            gate.set()
            t.join(5)
        self.assertFalse(piinstall.busy())

    def test_remove(self):
        piinstall.install(which=self.which(), run=Runner())
        self.assertTrue(os.path.isdir(pirun.MANAGED_DIR))
        self.assertEqual(piinstall.remove()[0], True)
        self.assertFalse(os.path.exists(pirun.MANAGED_DIR))
        self.assertEqual(piinstall.installed(), "")
        self.assertEqual(piinstall.remove()[0], True)     # nothing there is fine


class Background(Base):
    def test_start_runs_in_the_background_and_records_the_result(self):
        ok = piinstall.start("install", which=fake_which({"node": self.node}), run=Runner())
        self.assertTrue(ok)
        piinstall.wait(5)
        last = piinstall.last()
        self.assertEqual((last["action"], last["ok"]), ("install", True))
        self.assertIn("0.84.0", last["log"])
        self.assertTrue(piinstall.start("remove"))
        piinstall.wait(5)
        self.assertEqual((piinstall.last()["action"], piinstall.last()["ok"]), ("remove", True))
        self.assertEqual(piinstall.installed(), "")

    def test_start_refuses_unknown_actions(self):
        with self.assertRaises(ValueError):
            piinstall.start("format-c")


class Status(Base):
    def test_reports_everything_the_card_needs(self):
        which = fake_which({"node": self.node})
        s = piinstall.status("", which=which, run=Runner())
        self.assertEqual(s["node"]["version"], "24.14.0")
        self.assertEqual(s["min_node"], "22.19.0")
        self.assertEqual((s["managed"], s["active"], s["busy"]), ("", "", False))
        piinstall.install(which=which, run=Runner(version="0.85.1"))
        s = piinstall.status("", which=which, run=Runner())
        self.assertEqual((s["managed"], s["active"]), ("0.85.1", "managed"))
        self.assertIn("node_installable", s)
        self.assertIn("last", s)
        self.assertIn("hint", s)


class Routes(Base):
    def setUp(self):
        super().setUp()
        import routes
        self.routes = routes
        self.started = []
        self.addCleanup(setattr, piinstall, "start", piinstall.start)
        piinstall.start = lambda action, **kw: self.started.append(action) or True

    def test_status(self):
        status, out = self.routes.GET_ROUTES["/api/pi/status"](self.routes.Req())
        self.assertEqual(status, 200)
        self.assertIn("node", out)
        self.assertIn("managed", out)

    def test_install_and_remove_start_jobs(self):
        P = self.routes.POST_ROUTES
        self.assertEqual(P["/api/pi/install"](self.routes.Req())[1], {"started": True})
        self.assertEqual(P["/api/pi/remove"](self.routes.Req())[1], {"started": True})
        self.assertEqual(self.started, ["install", "remove"])

    def test_busy_is_a_conflict(self):
        piinstall.start = lambda action, **kw: False
        with self.assertRaises(self.routes.ApiError) as cm:
            self.routes.POST_ROUTES["/api/pi/install"](self.routes.Req())
        self.assertEqual(cm.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
