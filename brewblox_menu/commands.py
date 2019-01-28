"""
Entrypoint for the BrewBlox commands menu
"""

import platform
import shutil
import sys
from abc import ABC, abstractmethod
from distutils.util import strtobool
from os import getenv, path
from subprocess import STDOUT, CalledProcessError, check_call, check_output

from dotenv import find_dotenv, load_dotenv


def is_pi():
    return platform.machine().startswith('arm')


def base_dir():
    return path.dirname(__file__)


def docker_tag():
    return '{}{}'.format(
        'rpi-' if is_pi() else '',
        getenv('BREWBLOX_RELEASE', 'latest')
    )


def command_exists(cmd):
    return bool(shutil.which(cmd))


def confirm(question, default='y'):
    print('{} [Y/n]'.format(question))
    while True:
        try:
            return strtobool(input().lower() or default)
        except ValueError:
            print('Please respond with \'y(es)\' or \'n(o)\'.')


def check_ok(cmd):
    try:
        check_output(cmd, shell=True, stderr=STDOUT)
        return True
    except CalledProcessError:
        return False


def is_root():
    return check_ok('ls /root')


def is_docker_user():
    return check_ok('id -nG $USER | grep -qw "docker"')


class Command(ABC):
    def __init__(self, description, keyword):
        self.description = description
        self.keyword = keyword

    def __str__(self):
        return '{} {}'.format(self.keyword.ljust(15), self.description)

    def announce(self, shell_cmds):
        print('The following shell commands will be used: \n')
        for cmd in shell_cmds:
            print('\t', cmd)
        print('')
        input('Press ENTER to continue, Ctrl+C to cancel')

    def run(self, shell_cmd):
        print('\n' + 'Running command: \n\t', shell_cmd, '\n')
        return check_call(shell_cmd, shell=True, stderr=STDOUT)

    def run_all(self, shell_cmds, announce=True):
        if announce:
            self.announce(shell_cmds)
        return [self.run(cmd) for cmd in shell_cmds]

    @abstractmethod
    def action(self):
        pass


class ExitCommand(Command):
    def __init__(self):
        super().__init__('Exit this menu', 'exit')

    def action(self):
        raise SystemExit


class ComposeDownCommand(Command):
    def __init__(self):
        super().__init__('Stop running services', 'down')

    def action(self):
        cmd = 'docker-compose down'
        self.run_all([cmd])


class ComposeUpCommand(Command):
    def __init__(self):
        super().__init__('Start all services if not running', 'up')

    def action(self):
        cmd = 'docker-compose up -d'
        self.run_all([cmd])


class ComposeUpdateCommand(Command):
    def __init__(self):
        super().__init__('Update all services', 'update')

    def action(self):
        shell_commands = [
            'docker-compose down',
            'docker-compose pull',
            'docker-compose up -d',
        ]
        self.run_all(shell_commands)


class InstallCommand(Command):
    def __init__(self):
        super().__init__('Install a new BrewBlox system', 'install')

    def ask_target_dir(self):
        target_dir = input('In which directory do you want to install the BrewBlox configuration? [./brewblox]')
        target_dir = target_dir or './brewblox'
        target_dir = target_dir.rstrip('/')
        return target_dir

    def action(self):
        reboot_required = False
        shell_commands = [
            'sudo apt update',
            'sudo apt upgrade -y',
        ]

        if command_exists('docker'):
            print('Docker is already installed, skipping...')
        elif confirm('Do you want to install Docker?'):
            shell_commands.append('curl -sSL https://get.docker.com | sh')
            reboot_required = True

        if is_docker_user():
            print('{} already belongs to the Docker group, skipping...'.format(getenv('USER')))
        elif confirm('Do you want to run Docker commands without sudo?'):
            shell_commands.append('sudo usermod -aG docker $USER')
            reboot_required = True

        if command_exists('docker-compose'):
            print('docker-compose is already installed, skipping...')
        elif confirm('Do you want to install docker-compose (from pip)?'):
            shell_commands.append('sudo pip install -U docker-compose')

        source_dir = base_dir() + '/install_files'
        target_dir = self.ask_target_dir()
        source_compose = 'docker-compose_{}.yml'.format('armhf' if is_pi() else 'amd64')

        shell_commands += [
            'mkdir {}'.format(target_dir),
            'mkdir {}/couchdb'.format(target_dir),
            'mkdir {}/influxdb'.format(target_dir),
            'cp {}/{} {}/docker-compose.yml'.format(source_dir, source_compose, target_dir),
            'cp -r {}/traefik {}/'.format(source_dir, target_dir),
        ]

        if reboot_required and confirm('A reboot will be required, do you want to do so?'):
            shell_commands.append('sudo reboot')

        self.run_all(shell_commands)


class SetupCommand(Command):
    def __init__(self):
        super().__init__('Run first-time setup', 'setup')

    def action(self):
        host = 'https://localhost/datastore'
        database = 'brewblox-ui-store'
        presets_dir = '{}/presets'.format(base_dir())
        modules = ['services', 'dashboards', 'dashboard-items']

        shell_commands = [
            'docker-compose down',
            'docker-compose pull',
            'sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 ' +
            '-keyout traefik/brewblox.key ' +
            '-out traefik/brewblox.crt',
            'sudo chmod 644 traefik/brewblox.crt',
            'sudo chmod 600 traefik/brewblox.key',
            'docker-compose up -d datastore traefik',
            'sleep 5',
            'curl -Sk -X GET --retry 10 --retry-delay 5 {} > /dev/null'.format(host),
            'curl -Sk -X PUT {}/_users'.format(host),
            'curl -Sk -X PUT {}/{}'.format(host, database),
            *[
                'cat {}/{}.json '.format(presets_dir, mod) +
                '| curl -Sk -X POST ' +
                '--header \'Content-Type: application/json\' ' +
                '--header \'Accept: application/json\' ' +
                '--data "@-" {}/{}/_bulk_docs'.format(host, database)
                for mod in modules
            ],
            'docker-compose down',
        ]
        self.run_all(shell_commands)


class FirmwareFlashCommand(Command):
    def __init__(self):
        super().__init__('Flash firmware on Spark', 'flash')

    def action(self):
        tag = docker_tag()
        shell_commands = [
            'docker-compose down',
            'docker pull brewblox/firmware-flasher:{}'.format(tag),
            'docker run -it --rm --privileged brewblox/firmware-flasher:{} trigger-dfu'.format(tag),
            'sleep 2',
            'docker run -it --rm --privileged brewblox/firmware-flasher:{} flash'.format(tag),
        ]

        input('Please press ENTER when your Spark is connected over USB')
        self.run_all(shell_commands)


class BootloaderCommand(Command):
    def __init__(self):
        super().__init__('Flash bootloader on Spark', 'bootloader')

    def action(self):
        tag = docker_tag()
        shell_commands = [
            'docker-compose down',
            'docker pull brewblox/firmware-flasher:{}'.format(tag),
            'docker run -it --rm --privileged brewblox/firmware-flasher:{} flash-bootloader'.format(tag),
        ]

        input('Please press ENTER when your Spark is connected over USB')
        self.run_all(shell_commands)


class WiFiCommand(Command):
    def __init__(self):
        super().__init__('Connect Spark to WiFi', 'wifi')

    def action(self):
        tag = docker_tag()
        shell_commands = [
            'docker-compose down',
            'docker pull brewblox/firmware-flasher:{}'.format(tag),
            'sleep 2',
            'docker run -it --rm --privileged brewblox/firmware-flasher:{} wifi'.format(tag),
        ]

        input('Please press ENTER when your Spark is connected over USB')
        self.run_all(shell_commands)


class CheckStatusCommand(Command):
    def __init__(self):
        super().__init__('Check system status', 'status')

    def action(self):
        cmd = 'docker-compose ps'
        self.run_all([cmd])


class LogFileCommand(Command):
    def __init__(self):
        super().__init__('Write service logs to brewblox-log.txt', 'log')

    def action(self):
        shell_commands = [
            'date > brewblox-log.txt',
            'for svc in $(docker-compose ps --services | tr "\\n" " "); do ' +
            'docker-compose logs -t --no-color --tail 200 ${svc} >> brewblox-log.txt; ' +
            'echo \'\\n\' >> brewblox-log.txt; ' +
            'done;'
        ]
        self.run_all(shell_commands)


MENU = """
index - name         description
----------------------------------------------------------
{}
----------------------------------------------------------

Press Ctrl+C to exit.
"""


def main(args=...):
    load_dotenv(find_dotenv(usecwd=True))
    all_commands = [
        ComposeUpCommand(),
        ComposeDownCommand(),
        ComposeUpdateCommand(),
        InstallCommand(),
        SetupCommand(),
        FirmwareFlashCommand(),
        BootloaderCommand(),
        WiFiCommand(),
        CheckStatusCommand(),
        LogFileCommand(),
        ExitCommand(),
    ]
    command_descriptions = [
        '{} - {}'.format(str(idx+1).rjust(2), cmd)
        for idx, cmd in enumerate(all_commands)
    ]

    if is_root():
        print('The BrewBlox menu should not be run as root.')
        raise SystemExit

    if args is ...:
        args = sys.argv[1:]
    print('Welcome to the BrewBlox menu!')
    if args:
        print('Running commands: {}'.format(', '.join(args)))

    try:
        while True:
            print(MENU.format('\n'.join(command_descriptions)))
            try:
                arg = args.pop(0)
            except IndexError:
                arg = input('Please type a command name or index, and press ENTER. ')

            command = next(
                (cmd for idx, cmd in enumerate(all_commands) if arg in [cmd.keyword, str(idx+1)]),
                None,
            )

            if command:
                command.action()

                if not args:
                    break

    except CalledProcessError as ex:
        print('\n' + 'Error:', str(ex))

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
