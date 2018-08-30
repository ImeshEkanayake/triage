#!/usr/bin/env python
import yaml
import argparse
from descriptors import cachedproperty
from argcmdr import RootCommand, Command, main, cmdmethod
from sqlalchemy.engine.url import URL

from triage.component.architect.feature_generators import FeatureGenerator
from triage.component.audition import AuditionRunner
from triage.component.results_schema import upgrade_db, stamp_db, REVISION_MAPPING
from triage.experiments import CONFIG_VERSION, MultiCoreExperiment, SingleThreadedExperiment
from triage.util.db import create_engine

import logging

logging.basicConfig(level=logging.INFO)


def natural_number(value):
    natural = int(value)
    if natural <= 0:
        raise argparse.ArgumentTypeError(f"{value} is an invalid natural number")
    return natural


class Triage(RootCommand):
    """manage Triage database and experiments"""

    def __init__(self, parser):
        parser.add_argument(
            '-d', '--dbfile',
            default='database.yaml',
            type=argparse.FileType('r'),
            help="database connection file",
        )

    @cachedproperty
    def db_url(self):
        dbconfig = yaml.load(self.args.dbfile)
        db_url = URL(
                'postgres',
                host=dbconfig['host'],
                username=dbconfig['user'],
                database=dbconfig['db'],
                password=dbconfig['pass'],
                port=dbconfig['port'],
            )
        return db_url


@Triage.register
class Db(Command):
    """Manage experiment database"""

    @cmdmethod
    def upgrade(self, args):
        """Upgrade triage results database"""
        upgrade_db(args.dbfile)

    @cmdmethod('configversion', choices=REVISION_MAPPING.keys(), help='config version of last experiment you ran')
    def stamp(self, args):
        """Instruct the triage results database to mark itself as updated to a known version without doing any upgrading.
        
        Use this if the database was created without an 'alembic_version' table. Uses the config version of your experiment to infer what database version is suitable.
        """
        revision = REVISION_MAPPING[args.configversion]
        print(f"Based on config version {args.configversion} "
              f"we think your results schema is version {revision} and are upgrading to it")
        stamp_db(revision, args.dbfile)


@Triage.register
class Experiment(Command):
    """Validate and run experiments, manage experiment database"""

    def __init__(self, parser):
        parser.add_argument(
            '-c', '--config',
            type=argparse.FileType('r'),
            help="config file for Experiment",
            required=True
        )
        parser.add_argument(
            '--n-db-processes',
            type=natural_number,
            default=1,
            help="number of concurrent database connections to use"
        )
        parser.add_argument(
            '--n-processes',
            type=natural_number,
            default=1,
            help="number of cores to use"
        )
        parser.add_argument(
            '--project-path',
            type=str,
            help="path to store matrices and trained models"
        )
        parser.add_argument(
            '--replace',
            dest='replace',
            action='store_true'
        )
        parser.set_defaults(
            validate=True,
            validate_only=False,
        )

    @cachedproperty
    def experiment(self):
        db_url = self.root.db_url
        config = yaml.load(self.args.config)
        db_engine = create_engine(db_url)
        common_kwargs = {
            'db_engine': db_engine,
            'project_path': self.args.project_path,
            'config': config,
            'replace': self.args.replace,
        }
        if self.args.n_db_processes > 1 or self.args.n_processes > 1:
            experiment = MultiCoreExperiment(
                n_db_processes=self.args.n_db_processes,
                n_processes=self.args.n_processes,
                **common_kwargs
            )
        else:
            experiment = SingleThreadedExperiment(**common_kwargs)
        return experiment

    @cmdmethod('-v', '--validate', action='store_true', help="validate before running audition")
    @cmdmethod('--no-validate', action='store_false', dest='validate', help="run experiment without validation")
    @cmdmethod('--validate-only', action='store_true', help="only validate the config file not running Experiment")
    def run(self, args):
        if args.validate_only:
            self.experiment.validate()
        elif args.validate:
            self.experiment.validate()
            self.experiment.run()
        else:
            self.experiment.run()

    def __call__(self, args):
        self['run'](args)

    @cmdmethod
    def configversion(self, args):
        """Return the experiment config version compatible with this installation of Triage"""
        print(CONFIG_VERSION)

    @cmdmethod('as_of_date', help='The date as of which to run features')
    @cmdmethod('feature_config_file', type=argparse.FileType('r'), help='Feature config YAML file, containing a list of feature_aggregation objects')
    def featuretest(self, args):
        """Run a feature aggregation for an as-of-date"""
        db_engine = create_engine(self.root.db_url)
        feature_config = yaml.load(args.feature_config_file)

        FeatureGenerator(db_engine, 'features_test').create_features_before_imputation(
            feature_aggregation_config=feature_config,
            feature_dates=[args.as_of_date]
        )
        logging.info('Features created for feature_config %s and date %s', feature_config, args.as_of_date)


@Triage.register
class Audition(Command):
    """ Audition command to run or validate the config file
    """
    def __init__(self, parser):
        parser.add_argument(
            '-c', '--config',
            type=argparse.FileType('r'),
            default='audition_config.yaml',
            help="config file for audition",
        )
        parser.set_defaults(
            directory=None,
            validate=True,
        )

    @cachedproperty
    def runner(self):
        db_url = self.root.db_url
        dir_plot = self.args.directory
        config = yaml.load(self.args.config)
        db_engine = create_engine(db_url)
        return AuditionRunner(config, db_engine, dir_plot)

    def __call__(self, args):
        self['run'](args)

    @cmdmethod('-d', '--directory', default=None, help="directory to store the result plots from audition")
    @cmdmethod('-v', '--validate', action='store_true', help="validate before running audition")
    @cmdmethod('--no-validate', action='store_false', dest='validate', help="run audtion without validation")
    @cmdmethod('--validate-only', action='store_true', help="only validate the config file not running audition")
    def run(self, args):
        if args.validate_only:
            try:
                self.runner.validate()
            except Exception as err:
                raise(err)
        elif args.validate:
            try:
                self.runner.validate()
                self.runner.run()
            except Exception as err:
                raise(err)
        else:
            self.runner.run()


def execute():
    main(Triage)


if __name__ == '__main__':
    main(Triage)
