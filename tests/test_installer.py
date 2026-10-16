import json
import io
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import installer


class ServiceIdentityTests(unittest.TestCase):
    def test_named_launcher_preserves_arguments_and_exec_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "release space ' $not_expanded 中文"
            python = release / '.venv/bin/python'
            python.parent.mkdir(parents=True)
            probe = 'import os,sys,json; print(json.dumps({"pid":os.getpid(),"args":sys.argv[1:],"cwd":os.getcwd()}))'
            python.write_text('#!/bin/sh\nexec ' + shlex.quote(str(Path(sys.executable).resolve())) +
                              ' -c ' + shlex.quote(probe) + ' "$@"\n')
            python.chmod(0o755)
            data = Path(temp) / "data ' with spaces"
            with patch.object(installer.subprocess, 'run'):
                app = installer.create_service_app(release, data, 18779, 'fixture-id')
            info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            self.assertEqual(info['CFBundleDisplayName'], 'ModelBridge 模型连接服务')
            value = installer.plist_value(release, data, 18779, 'fixture-id')
            self.assertEqual(value['AssociatedBundleIdentifiers'], [info['CFBundleIdentifier']])
            for args, expected in [(value['ProgramArguments'], installer.service_arguments(release, data, 18779, 'fixture-id')[1:]),
                                   ([value['ProgramArguments'][0]], [str(release / 'installer.py'), 'open'])]:
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
                output, _ = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0)
                result = json.loads(output)
                self.assertEqual(result['pid'], proc.pid)
                self.assertEqual(result['args'], expected)
                self.assertEqual(Path(result['cwd']), release.resolve())

    def test_bundle_registration_failure_does_not_stop_existing_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / 'existing.plist'
            agent.write_bytes(b'original')
            old_manifest = {'port':18779, 'install_id':'old-id'}
            (root / 'installation.json').write_text(json.dumps(old_manifest))
            def run(command, **kwargs):
                if command[0] == installer.LSREGISTER:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)
            with patch.object(installer, 'root_dir', return_value=root), patch.object(installer, 'launch_agent', return_value=agent), patch.object(installer.sys, 'platform', 'darwin'), patch.object(installer, 'health', return_value=None), patch.object(installer.subprocess, 'run', side_effect=run) as commands:
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install('test-uv', open_console=False)
            self.assertEqual(agent.read_bytes(), b'original')
            self.assertFalse(any(call.args[0][0] == 'launchctl' for call in commands.call_args_list))
            self.assertEqual(json.loads((root / 'installation.json').read_text()), old_manifest)

    def check_repair_failure(self, active):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release, data = root / 'release', root / 'data'
            data.mkdir()
            (data / 'admin.json').write_text(json.dumps({'token':'local-fixture-token'}))
            manifest = {'release':str(release), 'port':18779, 'install_id':'old-id'}
            (root / 'installation.json').write_text(json.dumps(manifest))
            agent = root / 'agent.plist'
            old = plistlib.dumps({'Label':'com.modelbridge.fixture', 'ProgramArguments':installer.service_arguments(release, data, 18779, 'old-id')})
            agent.write_bytes(old)
            def run(command, **kwargs):
                if command[:2] == ['launchctl','bootstrap'] and agent.read_bytes() != old:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)
            from unittest.mock import Mock
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps({'requests':{'active':active}}).encode())
            with patch.object(installer, 'root_dir', return_value=root), patch.object(installer, 'launch_agent', return_value=agent), patch.object(installer, 'service_label', return_value='com.modelbridge.fixture'), patch.object(installer, 'health', return_value={'install_id':'old-id'}), patch.object(installer.urllib.request, 'build_opener', return_value=opener), patch.object(installer, 'create_service_app', return_value=root / 'test.app'), patch.object(installer, 'stop_service') as stop, patch.object(installer.subprocess, 'run', side_effect=run) as commands:
                with self.assertRaises(RuntimeError if active else subprocess.CalledProcessError):
                    installer.repair_service_name()
            self.assertEqual(agent.read_bytes(), old)
            self.assertEqual(json.loads((root / 'installation.json').read_text()), manifest)
            self.assertEqual(stop.call_count, 0 if active else 2)
            self.assertEqual(commands.call_count, 0 if active else 2)

    def test_name_repair_waits_for_active_requests(self):
        self.check_repair_failure(active=1)

    def test_failed_name_repair_restores_original_startup(self):
        self.check_repair_failure(active=0)


class InstallerRollbackTests(unittest.TestCase):
    def test_new_install_and_legacy_upgrade_select_correct_service(self):
        with tempfile.TemporaryDirectory() as temp, patch('installer.Path.home', return_value=Path(temp)):
            self.assertEqual(installer.root_dir().name, 'ModelBridge')
            self.assertEqual(installer.service_label(), 'com.modelbridge.local')
            legacy = Path(temp) / 'Library/Application Support/HairFree Bridge'
            legacy.mkdir(parents=True)
            (legacy / 'installation.json').write_text('{}')
            self.assertEqual(installer.root_dir(), legacy)
            self.assertEqual(installer.service_label(), 'com.hairfree.bridge')
            self.assertEqual(installer.launch_agent().name, 'com.hairfree.bridge.plist')

    def run_failure(self, failing_phase):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "data"
            root.mkdir()
            agent = Path(temp) / "agent.plist"
            original_plist = b"previous launch agent"
            agent.write_bytes(original_plist)
            original_manifest = {"version": "old", "port": 18766, "install_id": "old-id", "release": str(root / "old")}
            (root / "installation.json").write_text(json.dumps(original_manifest))
            commands, bootstrap_failed = [], False

            def run(command, **kwargs):
                nonlocal bootstrap_failed
                commands.append(command)
                if command[:2] == ["launchctl", "print"]:
                    return subprocess.CompletedProcess(command, 113)
                if failing_phase == "dependencies" and command[1:3] == ["pip", "sync"]:
                    raise subprocess.CalledProcessError(1, command)
                if failing_phase == "activation" and command[:2] == ["launchctl", "bootstrap"] and not bootstrap_failed:
                    bootstrap_failed = True
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)

            with patch("installer.root_dir", return_value=root), patch("installer.launch_agent", return_value=agent), patch("installer.sys.platform", "darwin"), patch("installer.health", return_value=None), patch("installer.subprocess.run", side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install("test-uv", open_console=False)
            self.assertEqual(agent.read_bytes(), original_plist)
            self.assertEqual(json.loads((root / "installation.json").read_text()), original_manifest)
            if failing_phase == "dependencies":
                self.assertFalse(any(cmd[0] == "launchctl" for cmd in commands))
            else:
                self.assertEqual(sum(cmd[:2] == ["launchctl", "bootstrap"] for cmd in commands), 2)

    def test_dependency_failure_keeps_old_service_untouched(self):
        self.run_failure("dependencies")

    def test_activation_failure_restores_previous_service(self):
        self.run_failure("activation")


class UpgradeIdleTests(unittest.TestCase):
    def test_active_upgrade_never_stops_or_replaces_old_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {"port":18779, "install_id":"old-id", "release":str(root / "old")}
            (root / "installation.json").write_text(json.dumps(manifest))
            agent = root / "agent.plist"
            agent.write_bytes(b"old startup")
            with patch.object(installer, "root_dir", return_value=root), patch.object(installer, "launch_agent", return_value=agent), patch.object(installer, "health", return_value={"install_id":"old-id"}), patch.object(installer, "create_service_app"), patch.object(installer.subprocess, "run"), patch.object(installer, "assert_idle", side_effect=RuntimeError("active")), patch.object(installer, "stop_service") as stop:
                with self.assertRaisesRegex(RuntimeError, "active"):
                    installer.install("fixture-uv", open_console=False)
            stop.assert_not_called()
            self.assertEqual(agent.read_bytes(), b"old startup")
            self.assertEqual(json.loads((root / "installation.json").read_text()), manifest)

class ResumeExistingTests(unittest.TestCase):
    def fixture(self, temp):
        root = Path(temp)
        release = root / 'releases/original'
        release.mkdir(parents=True)
        manifest = {'version':'0.3.0', 'port':18779, 'install_id':'original-id', 'release':str(release)}
        (root / 'installation.json').write_text(json.dumps(manifest))
        agent = root / 'agent.plist'
        return root, release, manifest, agent

    def test_healthy_existing_service_does_not_run_installer_or_launchctl(self):
        with tempfile.TemporaryDirectory() as temp:
            root, release, manifest, agent = self.fixture(temp)
            with patch.object(installer, 'root_dir', return_value=root), patch.object(installer, 'health', return_value={'install_id':'original-id'}), patch.object(installer.subprocess, 'run') as run:
                installer.start_existing()
            run.assert_not_called()
            self.assertEqual(json.loads((root / 'installation.json').read_text()), manifest)

    def test_stopped_release_restores_original_launchagent_without_new_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root, release, manifest, agent = self.fixture(temp)
            with patch.object(installer, 'service_label', return_value='com.modelbridge.fixture'):
                original = plistlib.dumps(installer.plist_value(release, root / 'data', 18779, 'original-id'))
            (root / 'disabled-launchagent.plist').write_bytes(original)
            def run(command, **kwargs):
                return subprocess.CompletedProcess(command, 113 if command[:2] == ['launchctl', 'print'] else 0)
            with patch.object(installer, 'root_dir', return_value=root), patch.object(installer, 'launch_agent', return_value=agent), patch.object(installer, 'service_label', return_value='com.modelbridge.fixture'), patch.object(installer, 'health', side_effect=[None, {'install_id':'original-id'}]), patch.object(installer.subprocess, 'run', side_effect=run) as commands:
                installer.start_existing()
            self.assertEqual(agent.read_bytes(), original)
            self.assertEqual(json.loads((root / 'installation.json').read_text()), manifest)
            self.assertEqual(list((root / 'releases').iterdir()), [release])
            self.assertFalse(any('pip' in c.args[0] or 'venv' in c.args[0] for c in commands.call_args_list))
            self.assertTrue(any(c.args[0][:2] == ['launchctl', 'bootstrap'] for c in commands.call_args_list))

    def test_unrecognized_startup_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root, release, manifest, agent = self.fixture(temp)
            original = plistlib.dumps({'Label':'someone.else', 'ProgramArguments':['/unknown']})
            agent.write_bytes(original)
            with patch.object(installer, 'root_dir', return_value=root), patch.object(installer, 'launch_agent', return_value=agent), patch.object(installer, 'health', return_value=None), patch.object(installer.subprocess, 'run') as commands:
                with self.assertRaisesRegex(RuntimeError, '不一致'):
                    installer.start_existing()
            self.assertEqual(agent.read_bytes(), original)
            self.assertFalse(any(c.args[0][0] == 'launchctl' for c in commands.call_args_list))

    def test_missing_dependencies_require_repair_without_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            root, release, manifest, agent = self.fixture(temp)
            with patch.object(installer, 'root_dir', return_value=root), patch.object(installer, 'launch_agent', return_value=agent), patch.object(installer, 'health', return_value=None), patch.object(installer.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['fixture-python'])) as commands:
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.start_existing()
            self.assertFalse(agent.exists())
            self.assertEqual(commands.call_count, 1)
