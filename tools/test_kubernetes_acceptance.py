"""The disposable acceptance runner must never target an ambient cluster."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy.kubernetes_acceptance import EphemeralCluster, NAMESPACE


class KubernetesAcceptanceIsolationTests(unittest.TestCase):
    def test_every_kubernetes_command_uses_private_configuration_and_context(self):
        with tempfile.TemporaryDirectory() as folder:
            cluster = EphemeralCluster(folder)
            args = cluster.kubectl_args('get', 'pods')
            self.assertEqual(args[args.index('--kubeconfig') + 1], str(Path(folder) / 'kubeconfig'))
            self.assertEqual(args[args.index('--context') + 1], 'kind-' + cluster.name)
            self.assertEqual(args[args.index('--namespace') + 1], NAMESPACE)
            with patch('deploy.kubernetes_acceptance.command', return_value='') as command:
                cluster.helm('list')
                argv = command.call_args.args[0]
                self.assertEqual(argv[argv.index('--kubeconfig') + 1], str(cluster.kubeconfig))
                self.assertEqual(argv[argv.index('--kube-context') + 1], cluster.context)
                self.assertEqual(argv[argv.index('--namespace') + 1], NAMESPACE)

    def test_existing_name_is_never_adopted_or_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            cluster = EphemeralCluster(folder)
            with patch('deploy.kubernetes_acceptance.command', return_value=cluster.name + '\n') as command:
                with self.assertRaises(ValueError):
                    cluster.create('patient-trajectory-matching:test')
                cluster.close()
                self.assertEqual(command.call_count, 1)
                self.assertEqual(command.call_args.args[0], ['kind', 'get', 'clusters'])

    def test_partial_creation_cleanup_deletes_only_this_runs_named_cluster(self):
        with tempfile.TemporaryDirectory() as folder:
            cluster = EphemeralCluster(folder)
            with patch('deploy.kubernetes_acceptance.command', side_effect=['', RuntimeError('create failed')]):
                with self.assertRaises(RuntimeError):
                    cluster.create('patient-trajectory-matching:test')
            with patch('deploy.kubernetes_acceptance.command', return_value='') as command:
                cluster.close()
                command.assert_called_once_with(['kind', 'delete', 'cluster', '--name', cluster.name], timeout=180)


if __name__ == '__main__':
    unittest.main()
