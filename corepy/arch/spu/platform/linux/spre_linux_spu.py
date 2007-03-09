# Copyright 2006-2007 The Trustees of Indiana University.

# This software is available for evaluation purposes only.  It may not be
# redistirubted or used for any other purposes without express written
# permission from the authors.

# Authors:
#   Christopher Mueller (chemuell@cs.indiana.edu)
#   Andrew Lumsdaine    (lums@cs.indiana.edu)


__doc__="""
SPE for the Cell SPU
"""

import array
import math

import corepy.spre.spe as spe
import spu_exec

# Set the path to the spu bootstrap object file
import os.path
spu_exec.set_bootstrap_path(os.path.join(os.path.split(__file__)[0], 'spu_bootstrap.o'))

ExecParams = spu_exec.ExecParams

import corepy.arch.spu.isa as spu
import corepy.arch.spu.lib.util as util

# ------------------------------
# Registers
# ------------------------------

class SPURegister(spe.Register): pass

# ------------------------------
# Constants
# ------------------------------

WORD_TYPE = 'I'           # array type that corresponds to 1 word
WORD_SIZE = 4             # size in bytes of one word
WORD_BITS = WORD_SIZE * 8 # number of bits in a word

INT_SIZES = {'b':1,  'c':1, 'h':2, 'i':4, 'B':1,  'H':2, 'I':4}

# ------------------------------
# Constants
# ------------------------------

# Parameters - (register, slot)
REG, SLOT = (0, 1)

spu_param_1 = (3, 1)
spu_param_2 = (3, 2)
spu_param_3 = (3, 3)

spu_param_4 = (4, 1)
spu_param_5 = (4, 2)
spu_param_6 = (4, 3)

spu_param_7 = (5, 0)
spu_param_8 = (5, 1)
spu_param_9 = (5, 2)
spu_param_10 = (5, 3)

N_SPUS = 6

# ------------------------------------------------------------
# Aligned Memory
# ------------------------------------------------------------

class aligned_memory(spu_exec.aligned_memory):
  def __init__(self, size, alignment = 128, typecode = 'B'):
    spu_exec.aligned_memory.__init__(self, size * INT_SIZES[typecode], alignment)
    self.typecode = typecode
    return

  def __str__(self): return '<aligned_memory typecode = %s addr = 0x%X size = %d ' % (
    self.typecode, self.get_addr(), self.get_size())
  
  def __len__(self):
    return self.get_size() / INT_SIZES[self.typecode]
  
  def buffer_info(self):
    return (self.get_addr(), self.get_size())

  def copy_to(self, source, size):
    return spu_exec.aligned_memory.copy_to(self, source, size * INT_SIZES[self.typecode])

  def copy_from(self, dest, size):
    return spu_exec.aligned_memory.copy_from(self, dest, size * INT_SIZES[self.typecode])

  def word_at(self, index, signed = False):
    """
    Minor hack to give fast access to data...
    TODO: full array-type interface?
    """
    # if signed:
    # return spu_exec.aligned_memory.signed_word_at(self, index * 4)
    return spu_exec.aligned_memory.word_at(self, index * 4)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def copy_param(code, target, source):
  """
  Copy a parameter from source reg to preferred slot in the target reg.
  For params in slot 0, this is just and add immediate.
  For params in other slots, the source is rotated.
  Note that other values in the source are copied, too.
  """
  if source[SLOT] != 0:
    code.add(spu.rotqbyi(target, source[REG], source[SLOT] * 4))
  else:
    code.add(spu.ai(target, source[REG], 0))
  return

ALIGN_UP = 0
ALIGN_DOWN = 1

def align_addr(addr, align = 16, dir = ALIGN_DOWN):
  """
  Round an address to the nearest aligned address based on align.
  Round up or down based on dir.
  """

  if dir == ALIGN_DOWN:
    return addr - (addr % align)
  else:
    return addr + (align - addr % align)
  
# ------------------------------------------------------------
# InstructionStream
# ------------------------------------------------------------

class InstructionStream(spe.InstructionStream):
  """
  SPU Instruction Stream.  
  Two assumptions:
    o We have the processor untill we're done
    o If we're prempted, the whole state is saved automagically

  Based on these and the fact that we are a leaf node, no register
  saves are attempted and only the raw instructions stream (no
  prologue/epilogue) is used.
  """

  # Class attributes
  RegisterFiles = (('gp', SPURegister, range(0,128)),)
  default_register_type = SPURegister
  
  exec_module   = spu_exec
  align         = 16 # 128 is max efficiency, 16 is what array currently does
  instruction_type  = WORD_TYPE
  
  def __init__(self, optimize=False):
    spe.InstructionStream.__init__(self)

    self._optimize = optimize
    self.code_offset = 0
    return

  # ------------------------------
  # Execute/ABI support
  # ------------------------------

  def _synthesize_prologue(self):
    """
    Setup register 0.
    """

    self._prologue = InstructionStream()
    
    # Reserve register r0 for the value zero
    self.acquire_register(reg = 0)
    util.load_word(self._prologue, 0, 0, zero = False)

    return

  def _synthesize_epilogue(self):
    """
    Do nothing.
    """

    return

  def cache_code(self):
    """
    Add a stop signal with return type 0x2000 (EXIT_SUCCESS) to the
    end if the instruction stream. (BE Handbook, p. 422).
    """

    # Generate the prologue
    self._synthesize_prologue()

    # Don't have a real epilogue.
    self.add(spu.stop(0x2000))
    # self._check_alignment(self._code, 'spu code')

    # self.exec_module.make_executable(self._code.buffer_info()[0], len(self._code))

    # Append our instructions to the prologue's, first making sure the alignment is correct.
    if len(self._prologue._code) % 2 == 1: # Odd number of instructions
      self._prologue.add(spu.lnop(0))

    self.code_offset = len(self._prologue._code)
    self._prologue._code.extend(self._code)
    self._prologue._check_alignment(self._prologue._code, 'spu prologue')
    
    self._epilogue = self    
    self._cached = True
    return


  def debug_set(self, idx, inst):
    self._prologue._code[idx + self.code_offset] = inst.render()
    self[idx] = inst
    return

  def add_return(self):
    """
    Do nothing.
    """
    return

  def add_jump(self, addr):
    """
    No nothing.
    """
    return

  def align_code(self, boundary):
    """
    Insert the appropraite nop/lnops to align the next instruction
    on the byte boudary.  boundary must be a multiple of four.
    """
    word_align = boundary / 4

    while len(self._code) % word_align:
      if len(self._code) % 2 == 0:
        self.add(spu.nop(0), True)
      else:
        self.add(spu.lnop(0), True)

    return

  def add(self, inst, optimize_override = False):

    if not optimize_override and self._optimize:
      # binary_string_inst = spu.DecToBin(inst)
      op = 'nop'
      # if binary_string_inst[0:3] in spu.inst_opcodes:
      #   op = spu.inst_opcodes[binary_string_inst[0:3]]
      # elif binary_string_inst[0:6] in spu.inst_opcodes:
      #   op = spu.inst_opcodes[binary_string_inst[0:6]]
      # elif binary_string_inst[0:7] in spu.inst_opcodes:
      #   op = spu.inst_opcodes[binary_string_inst[0:7]]
      # elif binary_string_inst[0:8] in spu.inst_opcodes:
      #   op = spu.inst_opcodes[binary_string_inst[0:8]]
      # elif binary_string_inst[0:9] in spu.inst_opcodes:
      #   op = spu.inst_opcodes[binary_string_inst[0:9]]
      # elif binary_string_inst[0:10] in spu.inst_opcodes:
      #   op = spu.inst_opcodes[binary_string_inst[0:10]]
        
      pipeline = inst.cycles[0]
        
      if (len(self._code) % 2 == 0) and pipeline == 0:   
        InstructionStream.add(self, inst)

      elif (len(self._code) % 2 == 1) and pipeline == 1:
        InstructionStream.add(self, inst)
      elif (len(self._code) % 2 == 0) and pipeline == 1:
        InstructionStream.add(self, spu.nop(0))
        InstructionStream.add(self, inst)
      elif (len(self._code) % 2 == 1) and pipeline == 0:
        InstructionStream.add(self, spu.lnop(0))
        InstructionStream.add(self, inst)

    else:
      spe.InstructionStream.add(self, inst)

    # Invalidate the cache
    self._cached = False
    return len(self._code)


class ParallelInstructionStream(InstructionStream):

  def __init__(self, optimize=False):
    InstructionStream.__init__(self, optimize)

    self.r_rank = self.acquire_register()
    self.r_size = self.acquire_register()

    self.r_block_size = None
    self.r_offset     = None

    # All the params are stored in r_rank
    self.r_params = self.r_rank

    # User/library supplied data size, used by processor to determine
    # block and offset for an execution run.  This value is in bytes.
    self.raw_data_size = None
    
    return

  def _synthesize_prologue(self):
    """
    Add raw_data_size/offest support code.
    """

    InstructionStream._synthesize_prologue(self)

    # Parallel parameters are passed in the prefered slot and the next
    # slot of the user arugment.
    self._prologue.add(spu.shlqbyi(self.r_rank, SPURegister(3, None), 4)) 
    self._prologue.add(spu.shlqbyi(self.r_size, SPURegister(3, None), 8)) 

    if self.raw_data_size is not None:
      self.acquire_block_registers()

      self._prologue.add(spu.shlqbyi(self.r_block_size, SPURegister(4, None), 4)) 
      self._prologue.add(spu.shlqbyi(self.r_offset, SPURegister(4, None), 8)) 
    else:
      print 'no raw data'
    return

  def acquire_block_registers(self):
    if self.r_block_size is None:
      self.r_block_size = self.acquire_register()
    if self.r_offset is None:
      self.r_offset     = self.acquire_register()

    # print 'offset/block_size', self.r_offset, self.r_block_size
    return
  
    
  def release_parallel_registers(self):
    self.release_register(self.r_rank)
    self.release_register(self.r_size)

    if self.r_block_size is not None:
      self.release_register(self.r_block_size)
    if self.r_offset is not None:
      self.release_register(self.r_offset)
      
    return



def _copy_params(params, rank, size):
  """
  Copy params.
  """
  ret = spu_exec.ExecParams()

  ret.addr = params.addr
  ret.p1 = rank
  ret.p2 = size
  ret.p3 = params.p3

  ret.size = params.size        

  ret.p4 = params.p4
  ret.p5 = params.p5
  ret.p6 = params.p6
  ret.p7 = params.p7
  ret.p8 = params.p8
  ret.p9 = params.p9
  ret.p10 = params.p10
  
  return ret


class Processor(spe.Processor):
  exec_module = spu_exec

  def execute(self, code, mode = 'int', debug = False, params = None, n_spus = 1):
    """
    Execute the instruction stream in the code object.

    Execution modes are:

      'int'  - return the intetger value in register gp_return when
               execution is complete
      'fp'   - return the floating point value in register fp_return
               when execution is complete
      'void' - return None
      'async'- execute the code in a new thread and return the thread
               id immediately

    If debug is True, the buffer address and code length are printed
    to stdout before execution.

    ParallelExecutionStream execution:
    
    If code is a ParallelInstructionStream code.n_spus threads are
    created and the parameter structure is set up with world_size=n_spus
    and rank values for each thread. A list containing the speids is
    returned.

    If raw_data_size is present and set on the code object, set the
    block_size and offset parameters.

    The parameters for parallel execution are:

      p1 = rank ($r3.2)
      p2 = size ($r3.3)

      p4 = block_size ($r4.2)
      p5 = offset     ($r4.3)
    
    """

    if len(code._code) == 0:
      return None

    # Cache the code here
    if not code._cached:
      code.cache_code()

    # Setup the parameter structure
    if params is None:
      params = spu_exec.ExecParams()

    addr = code._prologue.inst_addr()
    params.addr = addr
    params.size = len(code._prologue._code) * 4 # size in bytes

    retval = None

    if type(code) is ParallelInstructionStream:
      # Parallel SPU execution
      speids = []
      if n_spus > 6:
        raise Exception("Too many SPUs requests (%d > 6)" % n_spus)

      # print 'Regs:', code.r_rank, code.r_size, code.r_block_size, code.r_offset
      # Set up the parameters and execute each spu thread
      for i in range(n_spus):
        pi = _copy_params(params, i, n_spus)

        if hasattr(code, "raw_data_size") and code.raw_data_size is not None:
          pi.p4 = int(code.raw_data_size / n_spus)  # block_size
          pi.p5 = pi.p4 * i                         # offset

          # print 'Executing: 0x%x %d %d %d %d' % (pi.addr, pi.p1, pi.p2, pi.p4, pi.p5)
        speids.append(spe.Processor.execute(self, code, debug=debug, params=pi, mode='async'))

      # Handle blocking execution modes
      if mode != 'async':
        reterrs = [self.join(speid) for speid in speids]
        retval = reterrs
      else:
        retval = speids
    else:
      # Single SPU execution
      retval = spe.Processor.execute(self, code, mode, debug, params)

    return retval


DEBUG_STOP = 0xD

class DebugProcessor(spe.Processor):
  """
  Experimental class for simple debugging.
  """

  exec_module = spu_exec
  debug_stop = spu.stop(DEBUG_STOP, ignore_active = True)
  
  def __init__(self):
    spe.Processor.__init__(self)
    self.params = None
    self.spe_id = None
    self.code   = None

    self.ea  = None
    self.lsa = None 
    self.inst_size = None

    self.last_pc = None
    
    self.instructions = {} # key: inst, backup copy of we've replaced

    return
  
  def execute(self, code, mode = 'int', debug = False, params = None, n_spus = 1):

    if type(code) is ParallelInstructionStream:
      raise Exception('DebugProcessor does not support ParallelInstructionStream')

    self.code = code
    
    if len(code._code) == 0:
      return None

    # Add the two debug instructions
    self.debug_idx = self.code.size()
    self.code.add(spu.stop(DEBUG_STOP))

    self.debug_branch = self.code.size()    
    self.code.add(spu.stop(DEBUG_STOP))    

    # Cache the code here
    if not code._cached:
      code.cache_code()

    # Setup the parameter structure
    if params is None:
      params = spu_exec.ExecParams()

    addr = code._prologue.inst_addr()
    params.addr = addr
    params.size = len(code._prologue._code) * 4 # size in bytes

    self.params = params
    self.ea   = code._prologue.inst_addr()
    self.lsa  = (0x3FFFF - params.size) & 0xFFF80;
    self.size = params.size + (16 - params.size % 16);
    self.last_pc   = self.lsa
    self.last_stop = 1 

    self.debug_lsa = (self.lsa + self.code.code_offset * 4 + self.debug_idx * 4) >> 2

    mode = 'async'

    self.replace(self.last_stop, spu.bra(self.debug_lsa, ignore_active = True))

    self.spe_id = spe.Processor.execute(self, code, mode, debug, params)
    code.print_code()

    retval = self.wait_debug()
    
    return retval

  def replace(self, idx, inst):
    self.instructions[idx] = self.code.get_inst(idx) # self.code._prologue._code[idx]
    self.code.debug_set(idx, inst)
    return

  def restore(self, idx):
    # self.code._prologue._code[idx] = self.instructions[idx]
    self.code.debug_set(idx, self.instructions[idx])
    return

  def get_instructions(self):
    # return spe_mfc_getb(speid, ls, (void *)ea, size, tag, tid, rid);
    tag = 5
    ea = self.code._prologue.inst_addr()
    spu_exec.spu_getb(self.spe_id, self.lsa, ea, self.size, tag, 0, 0)
    spu_exec.read_tag_status_all(self.spe_id, 1 << tag);
    return

  def wait_debug(self):
    r = spu_exec.wait_stop_event(self.spe_id)
    if r != DEBUG_STOP:
      print 'Warning: SPU stopped for unknown reason:', r
    else:
      print 'Debug stop'
    return r

  def nexti(self):
    self.restore(self.last_stop)
    next_stop = self.last_stop + 1

    last_instruction = (next_stop == (self.debug_idx - 1))

    if not last_instruction:
      self.replace(next_stop,    spu.bra(self.debug_lsa, ignore_active = True))
      self.replace(self.debug_branch, spu.br(-(self.debug_lsa - self.last_stop), ignore_active = True))
      # self.replace(next_stop, self.debug_stop)
      
    self.get_instructions()
    self.code.print_code()
    self.resume(self.spe_id)

    if last_instruction:
      r = self.join(self.spe_id)
      r = None
    else:
      r = self.wait_debug()      
      self.last_stop = next_stop
    return r

  def dump_regs(self):
    current_pc = self.lsa + self.code.code_offset * 4 + self.last_stop * 4 
    next_inst = self.last_stop + 1    
    mbox   = 28 # write out mbox channel

    # Pseudo-code:
    #  1) Save code is: (do this as an array, not an instruction stream)
    save_size = 128 * 2 + 4
    save_code = array.array('I', range(save_size))
    
    for i in range(0, 128 * 2, 2):
      save_code[i] = spu.wrch(i / 2, mbox, ignore_active = True).render()
      save_code[i + 1] = spu.stop(0x6, ignore_active = True).render()

    # branch back to the debug stop
    save_code[128 * 2] = spu.stop(0x7, ignore_active = True).render()
    ret = spu.bra(self.debug_lsa, ignore_active = True)
    save_code[128 * 2 + 1] = ret.render()

    aligned_save_code = aligned_memory(save_size, typecode = 'I')
    aligned_save_code.copy_to(save_code.buffer_info()[0], len(save_code))

    #  2) Save lsa[0:len(save_code)]
    # TODO: do this with putb

    #  3) Push save code to lsa[0:]
    tag = 2
    spu_exec.spu_getb(self.spe_id, 0, aligned_save_code.buffer_info()[0], save_size * 4, tag, 0, 0)
    spu_exec.read_tag_status_all(self.spe_id, 1 << tag);
    
    #  3) Replace the debug branch with a branch to 0
    self.replace(self.debug_branch, spu.bra(0, ignore_active = True))
    self.get_instructions()

    #  4) Resume
    self.resume(self.spe_id)    

    #  5) Read the register values and send the ok signal
    regs = []
    for i in range(128):
      while spu_exec.stat_out_mbox(self.spe_id) == 0: pass
      value = spu_exec.read_out_mbox(self.spe_id)
      regs.append(value)

      r = spu_exec.wait_stop_event(self.spe_id)
      self.resume(self.spe_id)
      
    #  6) Restore code at original pc
    self.restore(next_inst)
    self.get_instructions()

    #  7) Restore lsa[0:len(save_code)]
    # TODO: do this with putb

    #  8) Resume
    r = spu_exec.wait_stop_event(self.spe_id)
    self.resume(self.spe_id)
    r = self.wait_debug()

    return regs

  def dump_mem(self):
    # Use putb to copy the local store to Python array
    return
    
# ------------------------------------------------------------
# Unit tests
# ------------------------------------------------------------

def TestInt():
  code = InstructionStream()
  proc = Processor()

  spu.set_active_code(code)
  
  r13 = code.acquire_register(reg = 13)
  r20 = code.acquire_register(reg = 20)
  spu.ai(r20, r20, 13)
  spu.ai(r13, r13, 13)
  spu.ai(r13, r13, 13)
  spu.ai(r13, r13, 13)
  spu.ai(r13, r13, 13)
  spu.ai(r13, r13, 13)
  
  spu.stop(0x200D)
  
  r = proc.execute(code) # , debug = True)
  assert(r == 13)
  print 'int result:', r
  # while True:
  #   pass
  return


def TestParams():
  # Run this with a stop instruction and examine the registers
  code = InstructionStream()
  proc = Processor()

  r_sum = code.acquire_register()
  r_current = code.acquire_register()

  # Zero the sum
  code.add(spu.xor(r_sum, r_sum, r_sum))
  
  for param in [spu_param_1, spu_param_2, spu_param_3, spu_param_4, spu_param_5,
                spu_param_6, spu_param_7, spu_param_8, spu_param_9, spu_param_10]:
    copy_param(code, r_current, param)
    code.add(spu.a(r_sum, r_sum, r_current))
    
  code.add(spu.ceqi(r_current, r_sum, 55))

  code.add(spu.brz(r_current, 2))
  code.add(spu.stop(0x200A))
  code.add(spu.stop(0x200B))
  
  params = spu_exec.ExecParams()

  params.p1  = 1 
  params.p2  = 2 
  params.p3  = 3 

  params.p4  = 4 
  params.p5  = 5 
  params.p6  = 6 

  params.p7  = 7 
  params.p8  = 8 
  params.p9  = 9 
  params.p10 = 10


  r = proc.execute(code, params = params)

  assert(r == 0xA)
  # print 'int result:', r
  # while True:
  #   pass
  return


def TestAlignedMemory():
  import spuiter
  n = 10000
  a = array.array('I', range(n))
  aa = aligned_memory(len(a), typecode='I')
  aa.copy_to(a.buffer_info()[0], len(a))

  # aa.print_memory()
  print str(aa), '0x%X, %d' % a.buffer_info()
  
  code = InstructionStream()
  proc = Processor()
  
  md = spuiter.memory_desc('I')
  md.from_array(aa)
  print str(md)
  md.get(code, 0)
  
  ls = spuiter.memory_desc('I', 0, n)
  seq_iter = spuiter.spu_vec_iter(code, ls)

  for i in seq_iter:
    i.v = i + i

  print str(md)
  md.put(code, 0)

  r = proc.execute(code, mode = 'int')
  # print a
  aa.copy_from(a.buffer_info()[0], len(a))
  # aa.print_memory()  
  print a[:20]
  print a[4090:4105]
  print a[8188:8200]    
  print a[-20:]  
  return

def TestParallel():
  # Run this with a stop instruction and examine the registers and memory
  code = ParallelInstructionStream()
  proc = Processor()

  code.raw_data_size = 128*8

  r = code.acquire_register()
  code.add(spu.ai(r, r, 0xCAFE))
  code.add(spu.ai(r, r, 0xBABE))    
  code.add(spu.stop(0x2000))

  r = proc.execute(code, mode='async', n_spus = 6)

  for speid in r:
    proc.join(speid)

  assert(True)
  return


def TestDebug():
  code = InstructionStream()
  proc = DebugProcessor()

  spu.set_active_code(code)
  
  ra = code.acquire_register()
  rb = code.acquire_register()

  spu.ai(ra, 0, 14)
  spu.ai(rb, 0, 13)
  spu.ai(rb, 0, 14)
  spu.ai(rb, 0, 15)
  spu.ai(rb, 0, 16)
  spu.ai(rb, 0, 17)
  
  spu.stop(0x200A)
  
  r = proc.execute(code) # , debug = True)

  r = proc.nexti()
  r = proc.nexti()
  r = proc.nexti()
  
  while r != None:
    regs = proc.dump_regs()
    print regs
    
    r = proc.nexti()
    
  assert(r == None)
  print 'int result:', r
  # while True:
  #   pass
  return




def TestOptimization():
  import time
  import spuiter
  import spuvar
  code1 = InstructionStream(optimize=False)
  code2 = InstructionStream(optimize=True)
  proc = Processor()
  for code in [code1, code2]:
    x = spuvar.spu_int_var(code, 0)
    y = spuvar.spu_int_var(code, 0)
    for i in spuiter.syn_iter(code, pow(2, 14)):
      x.v = x + x
      y.v = y + y
    s = time.time()
    proc.execute(code)
    e = time.time()
    print "Total time: ", e - s
  print "(First time is withOUT optimization.)"

def TestInt2(i0 = 0, i1 = 1):
  i2 = i0 + i1
  i3 = i1 + i2
  
  code = InstructionStream()
  proc = Processor()

  r_loop = 4
  r_address = 5
  r0 = 6
  r1 = 7
  r2 = 8
  r3 = 9
  
  # Load arguments into a quadword
  
  #################
  # Pack quadword #
  #################

  def load_value_int32(code, reg, value, clear = False):
    # obviously, value should be 32 bit integer
    code.add(spu.ilhu(reg, value / pow(2, 16)))      # immediate load halfword upper
    code.add(spu.iohl(reg, value % pow(2, 16))) # immediate or halfword lower
    if clear:
      code.add(spu.shlqbyi(reg, reg, 12)) # shift left qw by bytes, clears right bytes
    return

  load_value_int32(code, r0, i0, True)
  load_value_int32(code, r1, i1, True)
  code.add(spu.rotqbyi(r1, r1, 12)) # rotate qw by bytes
  load_value_int32(code, r2, i2, True)
  code.add(spu.rotqbyi(r2, r2, 8))
  load_value_int32(code, r3, i3, True)
  code.add(spu.rotqbyi(r3, r3, 4))
  code.add(spu.a(r0, r0, r1))
  code.add(spu.a(r0, r0, r2))
  code.add(spu.a(r0, r0, r3)) 

  ##########

  # Main loop to calculate Fibnoccai sequence

  load_value_int32(code, r_address, pow(2, 16), clear_bits = False) # start at 64K

  load_value_int32(code, r_loop, 0, clear_bits = False)
  start_label = code.size() + 1




  code.add(spu.sfi(r_loop, r_loop, 1))
  code.add(spu.brnz(r_loop, (-(next - start_label) * spu.WORD_SIZE)))

  #

  code.add(spu.stop(0x2005))

  r = proc.execute(code)
  # assert(r == 12)
  # print 'int result:', r

  return

if __name__ == '__main__':
  # TestDebug()
  TestInt()
  TestParams()
  # TestParallel()
  # TestOptimization()
  # TestAlignedMemory()
