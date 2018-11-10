from subprocess import Popen, PIPE
import os
import stat
import shutil
import signal
import uuid
import logging
import pwd
from plynx.constants import JobReturnStatus, NodeStatus, FileTypes, ParameterTypes
from plynx.db import Node, Output, Parameter
from plynx.utils.file_handler import get_file_stream, upload_file_stream
from plynx.utils.config import get_worker_config
from . import BaseNode

WORKER_CONFIG = get_worker_config()


class BaseBash(BaseNode):
    def __init__(self, node=None):
        super(BaseBash, self).__init__(node)
        self.sp = None

    def exec_script(self, script_location, logs, command='bash'):
        res = JobReturnStatus.SUCCESS

        try:
            pw_record = None
            if WORKER_CONFIG.user:
                pw_record = pwd.getpwnam(WORKER_CONFIG.user)

            def pre_exec():
                if WORKER_CONFIG.user:
                    user_uid = pw_record.pw_uid
                    user_gid = pw_record.pw_gid
                    os.setgid(user_gid)
                    os.setuid(user_uid)
                # Restore default signal disposition and invoke setsid
                for sig in ('SIGPIPE', 'SIGXFZ', 'SIGXFSZ'):
                    if hasattr(signal, sig):
                        signal.signal(getattr(signal, sig), signal.SIG_DFL)
                os.setsid()

            env = os.environ.copy()
            shutil.copyfile(script_location, logs['worker'])
            self.sp = Popen(
                [command, script_location],
                stdout=PIPE, stderr=PIPE,
                cwd='/tmp', env=env,
                preexec_fn=pre_exec)

            line = ''
            with open(logs['stdout'], 'w') as f:
                for line in iter(self.sp.stdout.readline, b''):
                    f.write(line)
            with open(logs['stderr'], 'w') as f:
                for line in iter(self.sp.stderr.readline, b''):
                    f.write(line)
            self.sp.wait()

            if self.sp.returncode:
                raise Exception("Process returned non-zero value")

        except Exception as e:
            res = JobReturnStatus.FAILED
            logging.exception("Job failed")
            with open(logs['worker'], 'a+') as worker_log_file:
                worker_log_file.write('\n' * 3)
                worker_log_file.write('#' * 60 + '\n')
                worker_log_file.write('JOB FAILED\n')
                worker_log_file.write('#' * 60 + '\n')
                worker_log_file.write(str(e))

        return res

    def kill_process(self):
        if hasattr(self, 'sp') and self.sp:
            logging.info('Sending SIGTERM signal to bash process group')
            try:
                os.killpg(os.getpgid(self.sp.pid), signal.SIGTERM)
            except OSError as e:
                logging.error('Error: {}'.format(e))

    # Hack: do not pickle file
    def __getstate__(self):
        d = dict(self.__dict__)
        if 'sp' in d:
            del d['sp']
        return d

    @classmethod
    def get_default(cls):
        node = Node()
        node.title = ''
        node.description = ''
        node.base_node_name = cls.get_base_name()
        node.node_status = NodeStatus.CREATED
        node.public = False
        node.parameters = [
            Parameter(
                name='cmd',
                parameter_type=ParameterTypes.TEXT,
                value='bash -c " "',
                mutable_type=False,
                publicable=False,
                removable=False
            ),
            Parameter(
                name='cacheable',
                parameter_type=ParameterTypes.BOOL,
                value=True,
                mutable_type=False,
                publicable=False,
                removable=False
            )
        ]
        node.logs = [
            Output(
                name='stderr',
                file_type=FileTypes.FILE,
                resource_id=None
            ),
            Output(
                name='stdout',
                file_type=FileTypes.FILE,
                resource_id=None
            ),
            Output(
                name='worker',
                file_type=FileTypes.FILE,
                resource_id=None
            )
        ]
        return node

    @staticmethod
    def _prepare_inputs(inputs, preview=False, pythonize=False):
        res = {}
        for input in inputs:
            filenames = []
            if preview:
                for i, value in enumerate(range(input.min_count)):
                    filename = os.path.join('/tmp', '{}_{}_{}'.format(str(uuid.uuid1())[:8], i, input.name))
                    filenames.append(filename)
            else:
                for i, value in enumerate(input.values):
                    filename = os.path.join('/tmp', '{}_{}_{}'.format(str(uuid.uuid1()), i, input.name))
                    with open(filename, 'wb') as f:
                        f.write(get_file_stream(value.resource_id).read())
                    if FileTypes.EXECUTABLE in input.file_types:
                        # `chmod +x` to the executable file
                        st = os.stat(filename)
                        os.chmod(filename, st.st_mode | stat.S_IEXEC)
                    filenames.append(filename)
            if pythonize:
                if input.min_count == 1 and input.max_count == 1:
                    res[input.name] = filenames[0]
                else:
                    res[input.name] = filenames
            else:
                # TODO is ' ' standard separator?
                res[input.name] = ' '.join(filenames)
        return res

    @staticmethod
    def _prepare_outputs(outputs, preview=False):
        res = {}
        for output in outputs:
            if preview:
                filename = os.path.join('/tmp', '{}_{}'.format(str(uuid.uuid1())[:8], output.name))
            else:
                filename = os.path.join('/tmp', '{}_{}'.format(str(uuid.uuid1()), output.name))
            res[output.name] = filename
        return res

    @staticmethod
    def _prepare_logs(logs):
        res = {}
        for log in logs:
            filename = os.path.join('/tmp', '{}_{}'.format(str(uuid.uuid1()), log.name))
            res[log.name] = filename
        return res

    @staticmethod
    def _get_script_fname(extension='.sh'):
        return os.path.join('/tmp', '{}_{}'.format(str(uuid.uuid1()), "exec{}".format(extension)))

    @staticmethod
    def _prepare_parameters(parameters, pythonize=False):
        res = {}
        for parameter in parameters:
            value = None
            if parameter.parameter_type == ParameterTypes.ENUM:
                index = max(0, min(len(parameter.value.values) - 1, parameter.value.index))
                value = parameter.value.values[index]
            elif parameter.parameter_type in [ParameterTypes.LIST_STR, ParameterTypes.LIST_INT]:
                if pythonize:
                    value = parameter.value
                else:
                    value = ' '.join(map(str, parameter.value))  # !!!!!!!!!
            elif parameter.parameter_type == ParameterTypes.CODE:
                value = parameter.value.value
            else:
                value = parameter.value
            res[parameter.name] = value
        return res

    def _postprocess_outputs(self, outputs):
        for key, filename in outputs.items():
            if os.path.exists(filename):
                with open(filename, 'rb') as f:
                    self.node.get_output_by_name(key).resource_id = upload_file_stream(f)

    def _postprocess_logs(self, logs):
        for key, filename in logs.items():
            if os.path.exists(filename) and os.stat(filename).st_size != 0:
                with open(filename, 'rb') as f:
                    self.node.get_log_by_name(key).resource_id = upload_file_stream(f)
