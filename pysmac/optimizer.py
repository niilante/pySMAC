import tempfile
import os
import shutil
import errno
import operator
import multiprocessing
import logging
import csv



import limit_resources
import remote_smac
from multiprocessing_wrapper import MyPool


class SMAC_optimizer(object):
   
	# collects smac specific data that goes into the scenario file
	def __init__(self, deterministic = True, t_limit_total_s=None, mem_limit_smac_mb=None, working_directory = None, persistent_files=False, debug = False):
		
		self.__logger = multiprocessing.log_to_stderr()
		if debug:
			self.__logger.setLevel(debug)
		else:
			self.__logger.setLevel(logging.WARNING)
		
		
		self.__t_limit_total_s = 0 if t_limit_total_s is None else int(t_limit_total_s)
		self.__mem_limit_smac_mb = None if (mem_limit_smac_mb is None) else int(mem_limit_smac_mb)
			
		self.__persistent_files = persistent_files
		
		
		# some basic consistency checks

		if (self.__t_limit_total_s < 0):
			raise ValueError('The total time limit cannot be nagative!')
		if (( self.__mem_limit_smac_mb is not None) and (self.__mem_limit_smac_mb <= 0)):
			raise ValueError('SMAC\'s memory limit has to be either None (no limit) or positive!')

		
		# create a temporary directory if none is specified
		if working_directory is None:
			self.working_directory = tempfile.mkdtemp()
		else:
			self.working_directory = working_directory
		
		# make some subdirs for output and smac internals
		self.__exec_dir = os.path.join(self.working_directory, 'exec')
		self.__out_dir  = os.path.join(self.working_directory, 'out' )

		for directory in [self.working_directory, self.__exec_dir, self.__out_dir]:
			try:
				os.makedirs(directory)
			except OSError as exception:
				if exception.errno != errno.EEXIST:
					raise
		
				
		# Set some of smac options
		# Most fields contain the standard values (as of SMAC 2.08.00).
		# All options from the smac manual can be accessed by
		# adding an entry to the dictionary with the appropriate name.
		# Some options will however have, at best, no effect, setting
		# others may even brake the communication.
		self.smac_options = {
			'algo-exec': 'echo 0',
			'run-obj': 'QUALITY',
			'algo-deterministic': deterministic,
			'validation': not deterministic,
			'cutoff_time': 3600,
			'intensification-percentage': 0.5,
			'num-pca': 7,
			'rf-full-tree-bootstrap': False,
			'rf-ignore-conditionality':False,
			'rf-num-trees': 10,
			'skip-features': True,
			'pcs-file': os.path.join(self.working_directory,'parameters.pcs'),
			'test-instances': os.path.join(self.working_directory ,'instances.dat'),
			'instances': os.path.join(self.working_directory ,'instances.dat'),
			'algo-exec-dir': self.working_directory,
			'output-dir': self.__out_dir,
			'console-log-level': 'OFF',
			'abort-on-first-run-crash': False,
			}
		if debug:
			self.smac_options['console-log-level']='INFO'

	# after SMAC finishes, some cleanup has to be done depending on persistent_files
	def __del__(self):
		if not self.__persistent_files:
			shutil.rmtree(self.working_directory)
	
	
	# find the minimum given a function handle and a specification of its parameters and optional
	# conditionals and forbidden clauses
	def minimize(self, func, max_evaluations, parameter_dict, 
			conditional_clauses = [], forbidden_clauses=[], 
			num_instances = None,  seed = None,  num_procs = 1, num_runs = 1,
			mem_limit_function_mb=None, t_limit_function_s= None):
		
		
		num_instances = None if (num_instances is None) else int(num_instances)
		if ((num_instances < 1) and (num_instances is not None)):
			raise ValueError('The number of instances must be positive!')

		num_procs = int(num_procs)
		pcs_string, parser_dict = remote_smac.process_parameter_definitions(parameter_dict)


		# adjust the seed variable
		if seed is None:
			seed = range(num_runs)
		elif isinstance(seed, int) and num_runs == 1:
			seed = [seed]
		elif isinstance(seed, int) and num_runs > 1:
			seed = range(seed, seed+num_runs)
		elif isinstance(seed, list) or isinstace(seed, tuple):
			if len(seed) != num_runs:
				raise ValueError, "You have to specify a seed for every instance!"
		else:
			raise ValueError, "The seed variable could not be properly processed!"
		
		
		self.smac_options['runcount-limit'] = max_evaluations
		
		# create and fill the pcs file
		with open(self.smac_options['pcs-file'], 'w') as fh:
			fh.write("\n".join(pcs_string + conditional_clauses + forbidden_clauses))
		
		#create and fill the instance file
		with open(self.smac_options['instances'], 'w') as fh:
			tmp_num_instances = 1 if num_instances is None else num_instances
			for i in range(tmp_num_instances):
				fh.write("id_%i\n"%i)

		# create and fill the scenario file
		scenario_fn = os.path.join(self.working_directory,'scenario.dat')
		with open(scenario_fn,'w') as fh:
			for name, value in self.smac_options.iteritems():
				fh.write('%s %s\n'%(name, value))
		
		# check that all files are actually present, so SMAC has everything to start
		assert all(map(os.path.exists, [scenario_fn, self.smac_options['pcs-file'], self.smac_options['instances']])), "Something went wrong creating files for SMAC! Try to specify a \'working_directory\' and set \'persistent_files=True\'."
		


		# create a pool of workers and make'em work
		pool = MyPool(num_procs)
		argument_lists = map(lambda s: [scenario_fn, s, func, parser_dict, self.__mem_limit_smac_mb, remote_smac.smac_classpath(),  num_instances, mem_limit_function_mb, t_limit_function_s, self.smac_options['algo-deterministic']], seed)
		
		pool.map(remote_smac.remote_smac_function, argument_lists)
		
		pool.close()
		pool.join()
		
		
			
		# find overall incumbent and return it
		scenario_dir = os.path.join(self.__out_dir,reduce(str, scenario_fn.split('/')[-1].split('.')[:-1]))
		
		run_incumbents = []
		
		for s in seed:
			with open( os.path.join(scenario_dir, 'traj-run-%i.txt'%s)) as csv_fh:
				try:
					csv_r = csv.reader(csv_fh)
					for row in csv_r:
						incumbent = row
					run_incumbents.append((float(incumbent[1]), map(lambda s: s.strip(" "), incumbent[5:])))
				except:
					pass
		
		run_incumbents.sort(key = operator.itemgetter(0))			
		
		conf_dict = {}
		for c in run_incumbents[0][1]:
			c = c.split('=')
			conf_dict[c[0]] = parser_dict[c[0]](c[1].strip("'"))
		return( run_incumbents[0][0], conf_dict )