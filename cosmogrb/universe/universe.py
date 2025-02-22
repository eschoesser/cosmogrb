import abc
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from tqdm import tqdm
from dask.distributed import progress

import numpy as np
import popsynth
from numpy.typing import ArrayLike
import gc

from cosmogrb.universe.survey import Survey
from cosmogrb.utils.logging import setup_logger

logger = setup_logger(__name__)


class Universe(object, metaclass=abc.ABCMeta):
    """Generate a Universe of GRBs from a population file (output popsynth)"""

    def __init__(
        self,
        population_file: str,
        grb_base_name: str = "SynthGRB",
        save_path: str = ".",
    ):

        """

        The generic universe object

        :param population_file: a popsynth population file name
        :type population_file: str
        :param grb_base_name: base name of saved GRB files
        :type grb_base_name: str
        :param save_path: path where GRB files are stored
        :type save_path: str
        :returns:

        """
        # we want to store the absolute path so that we can find it later
        self._population_file: Path = Path(population_file).absolute()

        self._is_processed: bool = False

        self._population: popsynth.Population = popsynth.Population.from_file(
            population_file
        ).to_sub_population()

        self._grb_base_name: str = grb_base_name

        self._save_path: Path = Path(save_path)

        assert sum(self._population.selection) == len(
            self._population.selection
        ), "The population seems to have had a prior selection on it. This is not good"

        # assign the number of GRBs

        self._n_grbs: int = len(self._population.selection)

        # build the GRBs

        self._name = [f"{self._grb_base_name}_{i}" for i in range(self._n_grbs)]

        logger.debug(f"The Universe contains {self._n_grbs} GRBs")

        self._local_parameters = {}

        self._parameter_servers = []

        self._process_populations()
        self._contstruct_parameter_servers()

    def _get_sky_coord(self) -> None:

        self._ra: ArrayLike = self._population.ra
        self._dec: ArrayLike = self._population.dec

    def _get_redshift(self) -> None:
        self._z: ArrayLike = self._population.distances

    def _get_duration(self) -> None:
        try:
            self._duration: ArrayLike = self._population.duration

        except:

            raise RuntimeError("The population must contain a duration value")

    def _contstruct_parameter_servers(self) -> None:

        for i in range(self._n_grbs):
            param_dict: Dict[str, float] = {}

            param_dict["z"] = self._z[i]
            param_dict["ra"] = self._ra[i]
            param_dict["dec"] = self._dec[i]
            param_dict["name"] = self._name[i]
            param_dict["duration"] = self._duration[i]

            # this is temporary
            param_dict["T0"] = 0.0

            for k, v in self._local_parameters.items():

                param_dict[k] = v[i]

            param_server: ParameterServer = self._parameter_server_type(
                **param_dict
            )

            file_name: Path = self._save_path / f"{self._name[i]}_store.h5"
            
            if file_name.exists():
                
                logger.info(f'{file_name} already exists')

            else:
                param_server.set_file_path(file_name)

                self._parameter_servers.append(param_server)

    def _process_populations(self) -> None:
        self._get_sky_coord()
        self._get_redshift()
        self._get_duration()

    def go(self, client=None) -> None:
        """
        Launch the creation of the Universe of GRBs.
        If no client is passed, it is done serially.

        :param client:
        :returns:
        :rtype:

        """

        if client is not None:
            
            chunk_size = 10  # Set your desired chunk size
            parameter_servers_future = client.scatter(self._parameter_servers)
            futures = client.map(self._grb_wrapper, parameter_servers_future)
            progress(futures)
            res = client.gather(futures)

            del futures
            del res
            gc.collect()

        else:

            res = [
                self._grb_wrapper(self._parameter_servers[i], serial=True)
                for i in tqdm(range(len(self._parameter_servers)),desc='All GRBs')
            ]
            gc.collect()

        self._is_processed = True

    def save(self, file_name: Union[str, Path]) -> None:
        """

        Save the infomation from the simulation to
        an HDF5 file

        :param file_name:
        :returns:
        :rtype:

        """

        if self._is_processed:

            grb_save_files = [
                (
                    self._save_path / f"{self._grb_base_name}_{i}_store.h5"
                ).absolute()
                for i in range(self._n_grbs)
            ]

            # create a survey file to save all the information from the run

            survey: Survey = Survey(
                grb_save_files=grb_save_files,
                population_file=self._population_file,
            )

            survey.write(file_name)

    @abc.abstractmethod
    def _grb_wrapper(self, parameter_server, serial=False):

        NotImplementedError()

    @abc.abstractmethod
    def _parameter_server_type(self):

        NotImplementedError()


class ParameterServer(object):
    def __init__(
        self,
        name: str,
        ra: float,
        dec: float,
        z: float,
        duration: float,
        T0: float,
        **kwargs,
    ):
        """FIXME! briefly describe function

        :param name:
        :param ra:
        :param dec:
        :param z:
        :param duration:
        :param T0:
        :returns:
        :rtype:

        """

        self._parameters: Dict[str, float] = dict(
            name=name, ra=ra, dec=dec, z=z, duration=duration, T0=T0
        )

        for k, v in kwargs.items():

            self._parameters[k] = v

        self._file_path: Optional[Path] = None

    @property
    def parameters(self) -> Dict[str, float]:
        return self._parameters

    def set_file_path(self, file_path: Path) -> None:

        self._file_path: Path = file_path

    @property
    def file_path(self) -> Path:
        return self._file_path

    def __repr__(self):

        sep = "\n"

        return sep.join([f"{k}: {v}" for k, v in self._parameters.items()])


class GRBWrapper(object, metaclass=abc.ABCMeta):
    def __init__(self, parameter_server, serial=False):

        # construct the grb

        grb = self._grb_type(**parameter_server.parameters)

        # if we are running this parallel

        if not serial:

            grb.go(client=None, serial=serial)

        # otherwise let the GRB know

        else:

            grb.go(serial=serial)

        grb.save(parameter_server.file_path, clean_up=True)

        del grb

    @abc.abstractmethod
    def _grb_type(self, **kwargs):

        raise NotImplementedError()
