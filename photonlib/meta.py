import h5py
import torch
import numpy as np

from tqdm import tqdm
from contextlib import contextmanager

class AABox:
    '''
    Axis-Aligned bounding box in the N-dim cartesian coordinate
    '''
    def __init__(self, ranges):
        '''
        Constructor
        
        Parameters
        ----------
        ranges : array-like
            shape (2,N) holding two N-dimentional points.
            The first point [0,:] is the minimum point of the bounding box.
            The second point [1,:] is the maximum point of the bounding box.
        '''
        self._ranges = torch.as_tensor(ranges, dtype=torch.float32)
        self._lengths = torch.diff(self._ranges).flatten()
    
    @property
    def ranges(self):
        '''
        Access the minimum ([0,:]) and the maximum ([1,:]) points of the bounding box.
        '''
        return self._ranges

    @property
    def lengths(self):
        '''
        Access the lengths of the box along each axis
        '''
        return self._lengths


    def norm_coord(self, pos):
        '''
        Transform the absolute position to the normalized (-1 to 1 along each axis) within the box
        Parameters
        ----------
        pos : (array-like)
            a (or an array of) position(s) in the absolute coordinate.

        Returns
        -------
        torch.Tensor 
            instance holding the positions in the normalized coordinate using the box
            definition (the range along each axis -1 to 1).
        '''
        pos = torch.as_tensor(pos)

        ranges = torch.as_tensor(self.ranges, device=pos.device)

        norm_pos = pos - ranges[:,0]
        norm_pos /= torch.as_tensor(self.lengths, device=pos.device)
        norm_pos *= 2.
        norm_pos -= 1.

        return norm_pos


    @classmethod
    def load(cls, cfg_or_fname):
        '''
        A factory method to construct an instance using a photon library file
        '''

        if isinstance(cfg_or_fname,dict):
            fname = cfg_or_fname['photonlib']['filepath']
        elif isinstance(cfg_or_fname,str):
            fname = cfg_or_fname
        else:
            raise TypeError('The argument must be a configuration dict or a filepath string')

        with h5py.File(fname, 'r') as f:
            ranges = torch.column_stack((
                torch.as_tensor(f['min'],dtype=torch.float32),
                torch.as_tensor(f['max'],dtype=torch.float32)
                )
            )
        return cls(ranges)



class VoxelMeta(AABox):
    '''
    A voxelized version of the AABox. Voxel size is uniform along each axis.
    This class introduces two ways to define a position within the specified volume
    (i.e. within AABox) in addition to the absolute positions.
    
    Index (or idx) ... an array of (N) represents a position by the voxel index along each axis.

    Voxel id (or voxel) ... a single integer uniquely assigned to every voxel.

    The attributes in this class provide ways to convert between these and the absolute coordinates.
    '''

    def __init__(self, shape, ranges):
        '''
        Constructor

        Parameters
        ----------
        shape  : array-like
            N-dim array specifying the voxel count along each axis.
        ranges : array-like
            (2,N) array given to the AABox constructor (see description).
        '''
        super().__init__(ranges)
        self._shape = torch.as_tensor(shape, dtype=torch.int64)
        self._voxel_size = torch.diff(self.ranges).flatten() / self.shape
       
    def __repr__(self):
        s = 'Meta'
        for i,var in enumerate('xyz'):
            bins = self.shape[i]
            x0, x1 = self.ranges[i]
            s += f' {var}:({x0},{x1},{bins})'
        return s

    def __len__(self):
        return torch.product(self.shape)

    @property
    def shape(self):
        return self._shape

    @property
    def voxel_size(self):
        return self._voxel_size
    
    @property
    def bins(self):
        output = tuple(
            torch.linspace(ranges[0], ranges[1], nbins)
            for ranges, nbins in zip(self.ranges, self.shape+1)
        )

        return output

    @property
    def bin_centers(self):
        centers = tuple((b[1:] + b[:-1]) / 2. for b in self.bins)
        return centers
    
    @property
    def norm_step_size(self):
        return 2. / self.shape


    def idx_to_voxel(self, idx):
        '''
        Converts from the index coordinate (N) to the voxel ID (1)

        Parameters
        ----------
        idx : array-like (2D or 3D)
            An array of positions in terms of voxel index along xyz axis

        Returns
        -------
        torch.Tensor
            A 1D array of voxel IDs corresponding to the input axis index(es)
        '''

        idx = torch.as_tensor(idx)

        if len(idx.shape) == 1:
            idx = idx[None,:]

        nx, ny = self.shape[:2]
        vox = idx[:,0] + idx[:,1]*nx + idx[:,2]*nx*ny

        return vox.squeeze()
    
    def voxel_to_idx(self, voxel):
        '''
        Converts from the voxel ID (1) to the index coordinate (N)

        Parameters
        ----------
        voxel : int or array-like (1D)
            A voxel ID or a list of voxel IDs

        Returns
        -------
        torch.Tensor
            A list of index IDs. Shape (3) if the input is a single point. Otherwise (-1,3).

        '''
        voxel = torch.as_tensor(voxel)
        nx, ny = self.shape[:2]

        idx = torch.column_stack([
            voxel % nx,
            torch.floor_divide(voxel, nx) % ny,
            torch.floor_divide(voxel, nx*ny)]
            )

        return idx.squeeze()

    
    def idx_to_coord(self, idx):
        '''
        Converts from the index coordinate (N) to the absolute coordinate (N)

        Parameters
        ----------
        idx : array-like (1D or 2D)
            An array of positions in terms of voxel index along xyz axis

        Returns
        -------
        torch.Tensor
            An array of corresponding positions in the absolute coordinate (at each voxel center)
        '''
        idx = torch.as_tensor(idx)

        voxel_size = torch.as_tensor(self.voxel_size, device=idx.device)
        ranges = torch.as_tensor(self.ranges, device=idx.device)
        coord = (idx+0.5) * voxel_size
        coord += ranges[:, 0]
        return coord


    def voxel_to_coord(self, voxel):
        '''
        Converts from the voxel ID (1) to the absolute coordinate (N)

        Parameters
        ----------
        voxel : int or array-like (1D)
            A voxel ID or a list of voxel IDs

        Returns
        -------
        torch.Tensor
            An array of corresponding positions in the absolute coordinate (at each voxel center)
        '''
        idx = self.voxel_to_idx(voxel)
        return self.idx_to_coord(idx)
    

    def coord_to_idx(self, coord):
        '''
        Converts from the absolute coordinate (N) to the index coordinate (N)

        Parameters
        ----------
        coord : array-like (1D or 2D)
            A (or an array of) position(s) in the absolute coordinate

        Returns
        torch.Tensor
            An array of corresponding voxels represented as index along xyz axis
        -------
        '''
        # TODO(2021-10-29 kvt) validate coord_to_idx
        # TODO(2021-10-29 kvt) check ranges
        coord = torch.as_tensor(coord)

        step = torch.as_tensor(self.voxel_size, device=coord.device)
        ranges = torch.as_tensor(self.ranges, device=coord.device)
        idx = (coord - ranges[:,0]) / step

        idx = self.as_int64(idx)
        idx[idx<0] = 0
        for axis in range(3):
            n = self.shape[axis]
            mask = idx[...,axis] >= n
            idx[mask,axis] = n-1

        return idx


    def coord_to_voxel(self, coord):
        '''
        Converts from the absolute coordinate (N) to the voxel ID (1)

        Parameters
        ----------
        coord : array-like (1D or 2D)
            A (or an array of) position(s) in the absolute coordinate

        Returns
        torch.Tensor
            An array of corresponding voxels represented as integer voxel ID
        '''
        idx = self.coord_to_idx(coord)
        vox = self.idx_to_voxel(idx)
        return vox


    def as_int64(self, idx):
        idx = idx.type(torch.int64)
        return idx
        

    @classmethod
    def load(cls, cfg_or_fname):
        '''
        A factory method to construct an instance using a photon library file

        Parameters
        ----------

        Returns
        -------
        '''

        if isinstance(cfg_or_fname,dict):
            fname = cfg_or_fname['photonlib']['filepath']
        elif isinstance(cfg_or_fname,str):
            fname = cfg_or_fname
        else:
            raise TypeError('The argument must be a configuration dict or a filepath string')

        with h5py.File(fname, 'r') as f:
            shape = torch.as_tensor(f['numvox'][:],dtype=torch.float32)
            ranges = torch.column_stack((
                torch.as_tensor(f['min'],dtype=torch.float32),
                torch.as_tensor(f['max'],dtype=torch.float32)
                )
            )
        return cls(shape, ranges)


    @staticmethod
    def select_axis(axis):
        axis_to_num = dict(x=0, y=1, z=2)
        
        if isinstance(axis, str) and axis in axis_to_num:
            axis = axis_to_num[axis]
            
        axis_others = [0, 1, 2]
        if axis not in axis_others:
            raise IndexError(f'unknown axis {axis}')
        axis_others.pop(axis)

        return axis, axis_others

    def idx_at(self, axis, i):
        axis, axis_others = self.select_axis(axis)
        axis_a, axis_b = axis_others

        grid = [None] * 3
        grid[axis] = i
        grid[axis_a] = torch.arange(self.shape[axis_a])
        grid[axis_b] = torch.arange(self.shape[axis_b])

        idx = torch.column_stack([g.flatten() for g in torch.meshgrid(*grid)])
        return idx
    
    def check_valid_idx(self, idx, return_components=False):
        idx = torch.as_tensor(idx)
        shape = torch.as_tensor(self.shape,device=idx.device)
        mask = (idx >= 0) & (idx < shape)

        if return_components:
            return mask

        return torch.all(mask, axis=-1)

    def digitize(self, x, axis):
        x = torch.as_tensor(x)
        axis = self.select_axis(axis)[0]
        n = self.shape[axis]

        xmin = self.ranges[axis, 0]
        step = self.voxel_size[axis]

        idx = self.as_int64((x - xmin) / step)

        # TODO: (2021-10-29 kvt) exception?
        idx[idx<0] = 0
        idx[idx>=n] = n-1

        return idx


