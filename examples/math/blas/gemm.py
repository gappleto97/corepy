# Matrix-Matrix Multiplication Examples

import Numeric
import time

# ------------------------------------------------------------
# Python
# ------------------------------------------------------------

def invalid(A, B, C): pass

def python_mm(A, B, C):
  """
  Naive pure Python implementation.
  """

  C[:,:] = C * 0
  
  for i in range(A.shape[0]):
    for j in range(A.shape[1]):
      for k in range(B.shape[1]):
        C[i][k] += A[i][j] * B[j][k]

  return

def numeric_mm(A, B, C, mc, kc, nc, mr=1, nr=1):
  C[:,:] = Numeric.matrixmultiply(A, B)
  return


def _gebp_opt1(A, B, C, nc):
  n = C.shape[1]

  # Assume B is packed
  # Pack A into tA
  
  for j in range(0, n, nc):
    # Load Bj into cache
    # Load Cj into cache

    # Cj += ABj + Cj
    ABj = Numeric.matrixmultiply(A, B[:,j:j+nc])
    Numeric.add(C[:,j:j+nc], ABj, C[:,j:j+nc])

    # Store Caux into memory
  return

def _gepp_blk_var1(A, B, C, mc, nc):
  m = C.shape[1]
  
  # Pack B into tB

  for i in range(0, m, mc):
    _gebp_opt1(A[i:i+mc,:], B, C[i:i+mc, :], nc)
  
  return

_MC, _KC, _NC = 64, 64, 128
# _MC, _KC, _NC = 1024, 1024, 1024

def numeric_gemm_var1(A, B, C, mc, kc, nc, mr=1, nr=1):
  """
  GEMM_VAR1, top branch 
  """
  
  m, n = C.shape
  k = A.shape[0]

  mc = min(mc, m)
  kc = min(kc, k)
  nc = min(nc, n)  
  
  for k in range(0, k, kc):
    _gepp_blk_var1(A[:,k:k+kc], B[k:k+kc,:], C, mc, nc)
  return


def numeric_gemm_var1_flat(A, B, C, mc, kc, nc, mr=1, nr=1):
  M, N = C.shape
  K = A.shape[0]

  mc = min(mc, M)
  kc = min(kc, K)
  nc = min(nc, N)  

  tA = Numeric.zeros((mc, kc), typecode = Numeric.Float)
  tB = Numeric.zeros((kc, N), typecode = Numeric.Float)  

  for k in range(0, K, kc):
    # Pack B into tB
    tB[:,:] = B[k:k+kc:,:]
    
    for i in range(0, M, mc):
      imc = i+mc
      # Pack A into tA
      tA[:,:] = A[i:imc,k:k+kc]
      
      for j in range(0, N): # , nc):
        # Cj += ABj + Cj
        # jnc = j+nc
        ABj = Numeric.matrixmultiply(tA, tB[:,j])
        Numeric.add(C[i:imc:,j], ABj, C[i:imc:,j])
        
        # Store Caux into memory
  return

def numeric_gemm_var1_row(A, B, C, mc, kc, nc, mr=1, nr=1):
  """
  Observations on flat/row:
  - Using the same basic approach and keeping the parameters
    equal,  flat is faster by about 100 MFlops.  This is
    counterintuitive, since row is optimized for the row major layout
    of the arrays and flat is optimized for column major layout.
  - The key to getting row to be faster lies in making sure the kernel
    operations really take advantage of the row layout by adjusting
    nc, which impacts row operations in the inner loop:
    - In Python, the final add has the largest effect on performance 
      - for flat, nc controls how much of each row is copied
      - for row,  nc controls how much of each row is copied
      - How much of each row is copied appears to have the biggest
        impact
  - In the end, turns out that doing vector/matrix multiply in the
    innermost loop is most fair.  Cache effects aren't really
    observable since matrixmultiply is already pretty fast.  Making
    the block sizes bigger just gives more to an already fast kernel.
  """
  M, N = C.shape
  K = A.shape[0]

  nc = min(nc, N)
  kc = min(kc, K)
  mc = min(mc, M)
  
  tA = Numeric.zeros((M, kc), typecode = Numeric.Float)
  tB = Numeric.zeros((kc, nc), typecode = Numeric.Float)  

  for k in range(0, K, kc):
    # Pack A into tA
    tA[:,:] = A[:,k:k+kc]

    for j in range(0, N, nc):
      jnc = j+nc
      # Pack B into tB
      tB[:,:] = B[k:k+kc, j:jnc]

      for i in range(0, M): # , mc):
        # Ci = AiB + Ci
        # imc = i+mc
        ABi = Numeric.matrixmultiply(tA[i,:], tB)
        Numeric.add(C[i,j:jnc], ABi, C[i,j:jnc])
        
        # Store Caux into memory
  return


# ------------------------------------------------------------
# Synthetic
# ------------------------------------------------------------

import corepy.arch.ppc.isa as ppc
import corepy.arch.ppc.platform as synppc
import corepy.arch.ppc.types.ppc_types as ppcvar

from corepy.arch.ppc.lib.iterators import syn_range, syn_iter, CTR

class SynPackB:

  def synthesize(self, code, tB, N):
    """
    Extract a block from B and pack it for fast access.

    tB is transposed.
    """
    old_code = ppc.get_active_code()
    ppc.set_active_code(code)

    code.add_storage(tB)
    
    nc, kc = tB.shape

    vN = ppcvar.UnsignedWord(N)
    vkc = ppcvar.UnsignedWord(kc)
    
    vB = ppcvar.UnsignedWord()
    vBi = ppcvar.UnsignedWord()

    bij = ppcvar.UnsignedWord()
    tbji = ppcvar.UnsignedWord()
    
    vb = ppcvar.DoubleFloat()

    vtB = ppcvar.UnsignedWord(synppc.array_address(tB))
    vB.copy_register(3) # First parameter
    
    for i in syn_iter(code, kc):
      vBi = i * vN
      
      for j in syn_iter(code, nc):
        bij.v  = (vBi + j) * 8
        tbji.v = (j * kc + i) * 8
        vb.load(vB, bij)
        vb.store(vtB, tbji)        

    ppc.set_active_code(old_code)        
    return

class SynGEPB:

  def reg_loop_4x4(self, a, b, c, mr, nr, p_tA, p_tB, A_row_stride, B_col_stride):
    a[0].load(p_tA, 0 * A_row_stride)
    b[0].load(p_tB, 0 * B_col_stride)

    c[0][0].v = ppcvar.fmadd(a[0], b[0], c[0][0]); 
    
    b[1].load(p_tB, 1 * B_col_stride)          
    a[1].load(p_tA, 1 * A_row_stride)
    
    c[0][1].v = ppcvar.fmadd(a[0], b[1], c[0][1]); 
    c[1][0].v = ppcvar.fmadd(a[1], b[0], c[1][0])
    
    a[2].load(p_tA, 2 * A_row_stride)
    b[2].load(p_tB, 2 * B_col_stride)          
    
    c[1][1].v = ppcvar.fmadd(a[1], b[1], c[1][1])
    c[2][0].v = ppcvar.fmadd(a[2], b[0], c[2][0])          
    
    c[0][2].v = ppcvar.fmadd(a[0], b[2], c[0][2]); 
    c[1][2].v = ppcvar.fmadd(a[1], b[2], c[1][2])
    
    c[2][1].v = ppcvar.fmadd(a[2], b[1], c[2][1])
    
    a[3].load(p_tA, 3 * A_row_stride)
    b[3].load(p_tB, 3 * B_col_stride)
    
    
    c[2][2].v = ppcvar.fmadd(a[2], b[2], c[2][2])
    
    c[3][0].v = ppcvar.fmadd(a[3], b[0], c[3][0])
    c[3][1].v = ppcvar.fmadd(a[3], b[1], c[3][1])
    c[3][2].v = ppcvar.fmadd(a[3], b[2], c[3][2])
    c[3][3].v = ppcvar.fmadd(a[3], b[3], c[3][3])
    
    c[0][3].v = ppcvar.fmadd(a[0], b[3], c[0][3]); 
    c[1][3].v = ppcvar.fmadd(a[1], b[3], c[1][3])
    c[2][3].v = ppcvar.fmadd(a[2], b[3], c[2][3])

    return

  def reg_loop_simple(self, a, b, c, mr, nr, p_tA, p_tB, A_row_stride, B_col_stride):
    # Load the next values from tA and tB -- generating loops
    for ai in range(mr):
      a[ai].load(p_tA, ai * A_row_stride)
    
    for bj in range(nr):
      b[bj].load(p_tB, bj * B_col_stride)
    
    # Update c -- generating loop
    for ci in range(mr):
      for cj in range(nr):
        c[ci][cj].v = ppcvar.fmadd(a[ci], b[cj], c[ci][cj])
          
    return

  def reg_loop_interleaved(self, a, b, c, mr, nr, p_tA, p_tB, A_row_stride, B_col_stride):
    # Generate the operations so that there is some overlap between
    # loads and computations.
    
    def load_a(i): a[i].load(p_tA, i * A_row_stride)
    def load_b(i): b[i].load(p_tB, i * B_col_stride)
    def compute(i,j): c[i][j].v = ppcvar.fmadd(a[i], b[j], c[i][j]); 
    
    load_a(0)
    load_b(0)          
    
    for ri in range(0, mr):
      compute(ri, ri)
      
      if ri < (mr - 1):
        load_a(ri+1)
        load_b(ri+1)
        
      for ci in range(0, ri):
        compute(ci, ri)
        
      for cj in range(0, ri):
        compute(ri, cj)     
    # /end for ri

    if mr > 1:
      compute(mr-1, mr-1)
    
    return

  def c_aux_save_simple(self, code, nc, mr, a, b, p_C, p_C_aux, C_col_stride):
    # Copy C_aux to C
    # ~620 MFlops
    for jj in syn_range(code, 0, nc * C_col_stride, C_col_stride):
      for ci in range(mr):
    
        a[ci].load(p_C[ci], jj)
        b[ci].load(p_C_aux[ci], jj)
    
        a[ci].v = a[ci] + b[ci]
        a[ci].store(p_C[ci], jj)
    
      # /end for ci
    # /end for jj
    return

  def synthesize(self, code, M, K, N, kc, nc, mr = 1, nr = 1, _transpose = False): 
    """
    tA is M  x nc
    tB is nc x kc
    C  is M  x nc
    I  is the current block column in C
    """

    def prefetch_load(A, B):  return ppc.dcbt(A, B)
    def prefetch_store(A, B): return ppc.dcbtst(A, B)

    old_code = ppc.get_active_code()
    ppc.set_active_code(code)

    # Constants
    C_col_stride = 8    
    C_row_stride = N * C_col_stride

    # (Assume A and B are packed)
    A_col_stride = 8
    A_row_stride = kc * A_col_stride

    # B_col_stride = 8
    # B_row_stride = nc * B_col_stride
    if _transpose:
      B_row_stride = 8
      B_col_stride = kc * B_row_stride
    else:
      B_col_stride = 8      
      B_row_stride = nc * B_col_stride

    # Address variables
    r_tA_addr = ppcvar.UnsignedWord()
    r_tB_addr = ppcvar.UnsignedWord()
    r_C_addr  = ppcvar.UnsignedWord()
    r_C_aux_addr  = ppcvar.UnsignedWord()    

    # Load addresses from function parameters
    ppc.addi(r_tA_addr, 3, 0)
    ppc.addi(r_tB_addr, 4, 0)
    ppc.addi(r_C_addr,  5, 0)
    ppc.addi(r_C_aux_addr,  6, 0)    
    
    p_tA = ppcvar.UnsignedWord()
    p_tB = ppcvar.UnsignedWord()
    p_C  = [ppcvar.UnsignedWord() for i in range(mr)]
    p_C_aux = [ppcvar.UnsignedWord() for i in range(mr)]

    JJ = ppcvar.UnsignedWord()
    
    # Set the array pointers 
    p_tA.v = r_tA_addr
    p_tB.v = r_tB_addr

    for ci in range(mr):
      p_C[ci].v = r_C_addr + ci * C_row_stride
      p_C_aux[ci].v = r_C_aux_addr + ci * (nc * C_col_stride)

    # Inner loop variables
    if mr != nr:
      raise Exception('mr (%d) should equal nr (%d)' % (mr, nr))

    a = [ppcvar.DoubleFloat() for i in range(mr)]
    b = [ppcvar.DoubleFloat() for j in range(nr)]

    a_pre = [ppcvar.DoubleFloat() for i in range(mr)]
    b_pre = [ppcvar.DoubleFloat() for j in range(nr)]

    c = [[ppcvar.DoubleFloat() for j in range(nr)] for i in range(mr)]

    # For each row in C
    for ii in syn_range(code, 0, M, mr):

      # Reset p_tB
      p_tB.v = r_tB_addr

      # For each column in C
      # for jj in syn_range(code, 0, nc * C_col_stride, C_col_stride * nr):
      for jj in syn_range(code, 0, nc, nr):

        # Set p_tA to the current row in tA
        p_tA.v = r_tA_addr + ii * A_row_stride # * mr
        # prefetch_load(0, p_tA)
        
        # Set p_tB to the current col in tB
        p_tB.v = r_tB_addr + jj * B_col_stride
        
        # Zero the c register block
        ppc.fsubx(c[0][0], c[0][0], c[0][0])
        for ci in range(mr):
          for cj in range(nr):
            c[ci][cj].copy_register(c[0][0])
            
        # Inner loop over k
        for k in syn_iter(code, kc, mode = CTR): # syn_range(code, 0, kc * 8, 8):
          # Load the next values from tA and tB -- generating loops
          for ai in range(mr):
            a[ai].load(p_tA, ai * A_row_stride)
    
          for bj in range(nr):
            b[bj].load(p_tB, bj * B_col_stride)
    
          # Update c -- generating loop
          for ci in range(mr):
            for cj in range(nr):
              c[ci][cj].v = ppcvar.fmadd(a[ci], b[cj], c[ci][cj])

          p_tA.v = p_tA + A_col_stride
          p_tB.v = p_tB + B_row_stride

        # /end for k

        # Save c_current to c_aux -- generating loop
        # (this is OK performance-wise)
        for ci in range(mr):
          for cj in range(nr):
            c[ci][cj].store(p_C_aux[ci], cj * C_col_stride)
            
        # Increment the sub-matrix in C_aux
        for ci in range(mr):
          p_C_aux[ci].v = p_C_aux[ci] + C_col_stride * nr

      # /end for jj

      # Reset p_C_aux
      for ci in range(mr):
        p_C_aux[ci].v = r_C_aux_addr + ci * (nc * C_col_stride)

      # Copy C_aux to C
      self.c_aux_save_simple(code, nc, mr, a, b, p_C, p_C_aux, C_col_stride)

      # Increment p_C 
      for ci in range(mr):
        p_C[ci].v = p_C[ci] + C_row_stride * mr
            
    # /end for ii

    ppc.set_active_code(old_code)
    return

def syn_gemm(A, B, C, mc, kc, nc, mr=1, nr=1):
  """
  """
  cgepb = synppc.InstructionStream()
  cpackb = synppc.InstructionStream()  
  proc = synppc.Processor()

  gepb = SynGEPB()  
  pm1 = synppc.ExecParams()
  pm2 = synppc.ExecParams()  

  packb = SynPackB()
  
  C_aux1 = Numeric.zeros((mr, nc), typecode=Numeric.Float)
  C_aux2 = Numeric.zeros((mr, nc), typecode=Numeric.Float)  

  M, N = C.shape
  M = M
  K = A.shape[0]

  nc = min(nc, N)
  kc = min(kc, K)
  mc = min(mc, M)

  cgepb.set_debug(True)
  gepb.synthesize(cgepb, M, K, N, kc, nc, mr, nr, _transpose = True)
  cgepb.cache_code()
  # cgepb.print_code()

  tA = Numeric.zeros((M, kc), typecode = Numeric.Float)
  tB = Numeric.zeros((kc, nc), typecode = Numeric.Float)

  # tB1 = Numeric.zeros((kc, nc), typecode = Numeric.Float) + 14.0
  tB1 = Numeric.zeros((nc, kc), typecode = Numeric.Float) + 14.0
  tB2 = Numeric.zeros((kc, nc), typecode = Numeric.Float) + 15.0

  cpackb.set_debug(True)
  packb.synthesize(cpackb, tB1, N)
  cpackb.cache_code()
  # cpackb.print_code()  

  pack_params = synppc.ExecParams()
  B_addr = synppc.array_address(B)

  C_addr1 = synppc.array_address(C)
  C_addr2 = synppc.array_address(C) + (N / 2) * 8

  pm1.p1 = synppc.array_address(tA)
  pm1.p2 = synppc.array_address(tB1)
  pm1.p3 = C_addr1
  pm1.p4 = synppc.array_address(C_aux1)  

  pm2.p1 = synppc.array_address(tA)
  pm2.p2 = synppc.array_address(tB2)
  pm2.p3 = C_addr2
  pm2.p4 = synppc.array_address(C_aux2)  

  nc8 = nc * 8
  total = 0.0

  start = time.time()

  # print 'Addresses: tA 0x%08X tB 0x%08X C 0x%08X' % (pm1.p1, pm1.p2, pm1.p3)
  k = 0
  for k in range(0, K, kc):
    # Pack A into tA
    tA[:,:] = A[:,k:k+kc]

    pm1.p3 = C_addr1
    # pm2.p3 = C_addr + nc8
    # pm2.p3 = C_addr2
    kN = k * N * 8
    for j in range(0, N * 8, nc * 8):
      # Pack B into tB
      # tB1[:,:] = Numeric.transpose(B[k:k+kc, j:j+nc])
      pack_params.p1 = B_addr + kN + j # (k * N + j) * 8
      t1 = proc.execute(cpackb, params = pack_params)
      # tB1[:,:] = B[k:k+kc, j:j+nc]

      # j2 = j + N/2
      # tB2[:,:] = B[k:k+kc, j2:j2+nc]      

      # start1 = time.time()
      t1 = proc.execute(cgepb, params = pm1) # , mode = 'async')
      # stop1  = time.time()
      # total += stop1 - start1
      # t2 = proc.execute(cgepb, params = pm2) # , mode = 'async')

      # proc.join(t1)
      # proc.join(t2)
      
      pm1.p3 += nc8 
      # pm2.p3 += nc8 

  end = time.time()

  return end - start


# ------------------------------------------------------------
# Test Helpers
# ------------------------------------------------------------

def _mflops(m, k, n, elapsed):
  return 2 * m * k * n / (elapsed * 1.0e6)

class _result:
  """
  Container for holding and printing reuslts.
  """

  def __init__(self, name, m, k, n, mc, kc, nc, times):
    self.name = name
    self.short_name = name[:3] + '...' + name[-10:]
    if len(self.short_name) < 20:
      self.short_name += ' ' * (20 - len(self.short_name))
    self.size = (m, k, n)
    self.blocks = (mc, kc, nc)
    self.m = m
    self.avg = reduce(lambda a,b: a + b, times) / float(len(times))
    self.min = min(times)
    self.max = max(times)

    self.avg_mflops = _mflops(m, k, n, self.avg)
    self.min_mflops = _mflops(m, k, n, self.max)
    self.max_mflops = _mflops(m, k, n, self.min)
    return

  def __str__(self, sep = '\t'):
    self.sep = sep
    return '%(short_name)s%(sep)s%(m)s%(sep)s%(blocks)s%(sep)s%(avg_mflops).4f%(sep)s%(min_mflops).4f%(sep)s%(max_mflops).4f%(sep)s' % \
           self.__dict__
  

def _validate(name, m, n, k, C, C_valid):
  """
  Validate by comparing the contents of C to the contents of C_valid
  The eq matrix should contain all 1s if the matrices are the same.
  """

  # Compare the matrices and flatten the results
  eq = C == C_valid
  eq.shape = (eq.shape[0] * eq.shape[1],)

  # Reduce the results: 1 is valid, 0 is not
  if Numeric.product(eq) == 0:
    eq.shape = C.shape
    for i, row in enumerate(eq[:]):
      # or i == 3 or i == 4
      # (i == 0 or i == 1 or i == 2 ) and 
      if Numeric.product(row) == 0:
        if (0 <= i <= 3): # (i == 99999):
          print 'row', i, 'failed'
          print C[i,:4]
          print C_valid[i,:4]
          print row[:4]
      else:
        print 'row', i, 'succeeded'
    raise Exception("Algorithm '%s' failed validation for %d x %d x %d matrices" %
                    (name, m, k, n))
  print name, 'passed'
  return

def run_alg(alg, A, B, C, mc, kc, nc, mr, nr, niters):
  """
  Time an experiment niters times.
  """
  times = []
  for i in range(niters):
    start = time.time()
    t = alg(A, B, C, mc, kc, nc, mr, nr)
    end   = time.time()

    if t is not None:
      times.append(t)
    else:
      times.append(end - start)

  return times

def create_matrices(m, k, n):
  A = Numeric.arange(m*k, typecode = Numeric.Float)
  B = Numeric.arange(k*n, typecode = Numeric.Float)
  C = Numeric.zeros(m*n, typecode = Numeric.Float)

  A.shape = (m, k)
  B.shape = (k, n)
  C.shape = (m, n)

  return A, B, C

# ------------------------------------------------------------
# Unit Tests
# ------------------------------------------------------------

def test_gebp_opt1():
  m, k, n = (32, 32, 128)
  A, B, C = create_matrices(m, k, n)
  nc = 32

  _gebp_opt1(A, B, C, nc)

  C_valid = Numeric.matrixmultiply(A, B)

  _validate('test_gebp_opt1', m,n,k, C, C_valid)
  return


def test_syn_gepb():
  gepb = SynGEPB()
  code = synppc.InstructionStream()
  code.set_debug(True)
  
  m, k, n = (128, 32, 32)
  A, B, C = create_matrices(m, k, n)
  kc = k
  nc = 32

  mr = 4
  nr = 4
  
  C_aux = Numeric.zeros((mr, nc), typecode=Numeric.Float) # + 13.0
  
  A_addr = synppc.array_address(A)
  B_addr = synppc.array_address(B)
  C_addr = synppc.array_address(C)
  C_aux_addr = synppc.array_address(C_aux)

  gepb.synthesize(code, m, k, n, kc, nc, mr, nr) # , A_addr, B_addr, C_addr)

  # code.print_code()
  
  params = synppc.ExecParams()
  params.p1 = A_addr
  params.p2 = B_addr
  params.p3 = C_addr
  params.p4 = C_aux_addr  
  
  # code.print_code()
  proc = synppc.Processor()
  proc.execute(code, params = params)

  C_valid = Numeric.matrixmultiply(A, B)

  _validate('syn_gepb', m,n,k, C, C_valid)
  return

def test_gepp_blk_var1():
  m, k, n = (128, 128, 128)
  A, B, C = create_matrices(m, k, n)

  mc = 32
  nc = 32

  _gepp_blk_var1(A[:,0:mc], B[0:mc,:], C, mc, nc)

  C_valid = Numeric.zeros((m, n), typecode=Numeric.Float)
  for i in range(0, m, mc):
    C_valid[i:i+mc,:] = Numeric.matrixmultiply(A[i:i+mc,0:mc], B[0:mc,:])

  _validate('test_gepp_blk_var1', m,n,k, C, C_valid)
  return

def test_numeric_gemm_var1():
  # m, k, n = (128, 128, 128)
  m, k, n = (8, 8, 8)
  A, B, C = create_matrices(m, k, n)

  mc = 32
  kc = 32
  nc = 32

  numeric_gemm_var1(A, B, C, mc, kc, nc)

  C_valid = Numeric.matrixmultiply(A, B)
  
  _validate('test_gepp_blk_var1', m,n,k, C, C_valid)
  return

def test_numeric_gemm_var1_flat():
  m, k, n = (256, 256, 256)
  # m, k, n = (8, 8, 8)
  A, B, C = create_matrices(m, k, n)

  mc = 32
  kc = 32
  nc = 32

  numeric_gemm_var1_flat(A, B, C, mc, kc, nc)

  C_valid = Numeric.matrixmultiply(A, B)
  
  _validate('test_gepp_blk_var1', m,n,k, C, C_valid)
  return

def test_numeric_gemm_var1_row():
  m, k, n = (256, 256, 256)
  # m, k, n = (8, 8, 8)
  A, B, C = create_matrices(m, k, n)

  nc = 32
  kc = 32
  mr = 1
  nr = 1
  numeric_gemm_var1_row(A, B, C, nc, kc, mr, nr)

  C_valid = Numeric.matrixmultiply(A, B)
  
  _validate('test_gepp_blk_var1', m,n,k, C, C_valid)
  return

def test_syn_gemm():
  # m, k, n = (512, 512, 512)
  m, k, n = (256, 256, 256)
  # m, k, n = (128, 128, 128)
  # m, k, n = (64, 64, 64)
  
  # m, k, n = (8, 8, 8)
  A, B, C = create_matrices(m, k, n)

  mc = 32
  kc = 32
  nc = 64
  
  mr = 4
  nr = 4

  syn_gemm(A, B, C, mc, kc, nc, mr = mr, nr = nr)

  C_valid = Numeric.matrixmultiply(A, B)
  
  _validate('syn_gemm', m,n,k, C, C_valid)
  return


def test_syn_pack_b():

  # Create a 10x10 B array of indices
  B = Numeric.zeros((10, 10), typecode = Numeric.Float)
  a = Numeric.arange(10)

  for i in range(10):
    B[i,:] = a + i * 10

  B.shape = (10,10)

  # Create the packed array
  tB = Numeric.arange(25, typecode = Numeric.Float) * 0.0
  tB.shape = (5,5)

  B_offset = 3 * 10 + 0

  K, N = B.shape
  nc, kc = tB.shape

  pack_b = SynPackB()
  
  code = synppc.InstructionStream()
  proc = synppc.Processor()
  params = synppc.ExecParams()

  pack_b.synthesize(code, tB, N)
  
  params.p1 = synppc.array_address(B) + B_offset * 8

  proc.execute(code, params = params)
  
  
  # Validate
  B.shape  = (K * N,)

  tB_valid = Numeric.arange(nc*kc, typecode = Numeric.Float) * 0.0

  for i in range(kc):
    B_row = B_offset + i * N
    for j in range(nc):
      b  = B_row + j
      tb = j * kc + i
      tB_valid[tb] = B[b]
        
  tB_valid.shape = (nc,kc)
  B.shape  = (K, N)

  _validate('syn_pack_b', nc, nc, N, tB, tB_valid)

  return

def test(algs, niters = 2, validate = False):
  """
  Test a numcer of algorithms and return the results.
  """

  if type(algs) is not list:
    algs = [algs]

  results = []
  m,n,k = (64,64,64)

  # Cache effects show up between 2048 and 4096 on a G4
  tests = [8, 16, 32, 64] # , 128, 256, 512, 768, 1024, 2048, 4096]: [2048, 4096]: #
  tests = [128, 256, 512] # , 1024, 2048] #, 4096]
  # tests = [1024]
  tests = [512]
  for size in tests: 
    m,n,k = (size, size, size)
    A, B, C = create_matrices(m, k, n)

    if validate:
      C_valid = Numeric.matrixmultiply(A, B)

    for alg in algs:
      Numeric.multiply(C, 0, C)
      print alg.func_name, size
      if validate:
        alg(A, B, C)
        _validate(alg.func_name, m, n, k, C, C_valid)

      KC = [32, 64, 128, 256] # , 512]
      KC = [32, 64, 128, 256] # , 256] # , 512]      
      KC = [64]
      KC = [128]
      # KC = [128, 128, 128]      
      # NC = [32, 64, 128, 256] # , 512]
      NC = [32, 32, 32] 
      # NC = [256, 256, 256]

      for mc in [32]: # , 64, 128, 256]:
        for kc in KC:
          for nc in NC:
            print '  ', mc, kc, nc
            times = run_alg(alg, A, B, C, mc, kc, nc, 4, 4, niters)
            result = _result(alg.func_name, m, k, n, mc, kc, nc, times)
            results.append(result)

  return results

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
  import sys
  colors = 'rgbko'

  # algs = [numeric_mm, numeric_gemm_var1_flat, numeric_gemm_var1_row, syn_gemm]
  algs = [syn_gemm]  
  # algs = [numeric_gemm_var1_flat, numeric_gemm_var1_row]
  results = test(algs)

  if 'plot' in sys.argv:
    import pylab
    algs = {} # alg: [max...]
    size = [] # m

    for result in results:
      if not algs.has_key(result.name):
        algs[result.name] = []

      algs[result.name].append(result.max_mflops)

      if result.size[0] not in size:
        size.append(result.size[0])

    for i, alg in enumerate(algs.keys()):
      times = algs[alg]
      lines = pylab.plot(size, times)
      pylab.setp(lines, color=colors[i])
      print alg, colors[i]
    pylab.show()
  else:
    print 'Algorithm           \tSize\tBlocks        \tAvg      \tMin      \tMax      \t'

    for result in results:
      print str(result)

  return

if __name__=='__main__':
  # test_gebp_opt1()
  # test_gepp_blk_var1()
  # test_numeric_gemm_var1()
  # test_numeric_gemm_var1_flat()
  # test_numeric_gemm_var1_row()
  # test_syn_gepb()
  # test_syn_gemm()
  # test_syn_pack_b()
  main()
  # proto_reg_mm_4x4()
  
  
