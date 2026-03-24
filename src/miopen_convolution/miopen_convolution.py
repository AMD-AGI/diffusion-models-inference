from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class MIOpenConvolution:
    """
    Represents a convolution problem with all parameters.
    Handles default values for optional parameters.
    """

    MIOPENDRIVER_FLAGS = {
        'batchsize': ('-n', '--batchsize'),
        'in_channels': ('-c', '--in_channels'),
        'in_h': ('-H', '--in_h'),
        'in_w': ('-W', '--in_w'),
        'in_d': ('-!', '--in_d'),
        'out_channels': ('-k', '--out_channels'),
        'fil_h': ('-y', '--fil_h'),
        'fil_w': ('-x', '--fil_w'),
        'fil_d': ('-@', '--fil_d'),
        'pad_h': ('-p', '--pad_h'),
        'pad_w': ('-q', '--pad_w'),
        'pad_d': ('-$', '--pad_d'),
        'conv_stride_h': ('-u', '--conv_stride_h'),
        'conv_stride_w': ('-v', '--conv_stride_w'),
        'conv_stride_d': ('-#', '--conv_stride_d'),
        'dilation_h': ('-l', '--dilation_h'),
        'dilation_w': ('-j', '--dilation_w'),
        'dilation_d': ('-^', '--dilation_d'),
        'group_count': ('-g', '--group_count'),
        'bias': ('-b', '--bias'),
        'direction': ('-F', '--forw'),
        'in_layout': ('-I', '--in_layout'),
        'out_layout': ('-O', '--out_layout'),
        'fil_layout': ('-f', '--fil_layout'),
    }
    
    batchsize: int = 1
    in_channels: int = 0
    in_h: int = 0
    in_w: int = 0
    out_channels: int = 0
    fil_h: int = 3
    fil_w: int = 3
    in_d: Optional[int] = None
    fil_d: Optional[int] = None
    pad_h: int = 0
    pad_w: int = 0
    pad_d: int = 0
    conv_stride_h: int = 1
    conv_stride_w: int = 1
    conv_stride_d: int = 1
    dilation_h: int = 1
    dilation_w: int = 1
    dilation_d: int = 1
    group_count: int = 1
    bias: int = 0
    in_layout: Optional[str] = None
    fil_layout: Optional[str] = None
    out_layout: Optional[str] = None
    precision: str = 'FP32'
    direction: str = 'F'
    
    @staticmethod
    def get_spatial_dim(tokens: list):
        """Return whether fdb_key is 2D or 3D
        
        This function figures out the dimensionality by counting the
        number of "x" in the token representing filter-size.

        Note: function lifted from MIOpenFF repo
        """
        if tokens[3].count('x') == 1:
            return 2
        elif tokens[4].count('x') == 2:
            return 3
        else:
            return None
    
    @staticmethod
    def validate_db_key_schema(fdb_key: str) -> None:
        """Validate that a database key has the correct schema/format.
        
        Args:
            fdb_key (str): The fdb_key string to validate
            
        Raises:
            ValueError
        """
        if not fdb_key or not fdb_key.strip():
            print("Skipping empty database key")
            raise ValueError("Empty database key")
        
        key_part = fdb_key.split('=')[0]
        tokens = key_part.split('-')
        
        try:
            spatial_dim = MIOpenConvolution.get_spatial_dim(tokens)
        except (IndexError, AttributeError) as e:
            raise ValueError(
                f"Cannot determine spatial dimension for {fdb_key[:100]}: {e}"
            )

        valid_layouts = {'NCHW', 'NHWC', 'NCDHW', 'NDHWC'}
        
        try:
            if spatial_dim == 2:
                if len(tokens) == 15:
                    layout = tokens[12]
                    if layout not in valid_layouts:
                        raise ValueError(
                            f"Invalid layout field, expected single layout, got {layout}"
                        )
                elif len(tokens) == 17:
                    for idx, layout_name in [(12, 'in_layout'), (13, 'fil_layout'), (14, 'out_layout')]:
                        layout = tokens[idx]
                        if layout not in valid_layouts:
                            raise ValueError(
                                f"Invalid {layout_name} field, got {layout}"
                            )
            else:
                if len(tokens) == 17:
                    layout = tokens[14]
                    if layout not in valid_layouts:
                        raise ValueError(
                            f"Invalid layout field, expected single layout got {layout}"
                        )
                elif len(tokens) == 19:
                    for idx, layout_name in [(14, 'in_layout'), (15, 'fil_layout'), (16, 'out_layout')]:
                        layout = tokens[idx]
                        if layout not in valid_layouts:
                            raise ValueError(
                                f"Invalid {layout_name} field, got {layout}"
                            )
        except IndexError as e:
            raise ValueError(
                f"Invalid fdb_key schema"
                f"Key: {fdb_key[:100]}, Error: {e}"
            )
    
    @staticmethod
    def fdb_key_to_dict(fdb_key: str) -> Dict[str, Any]:
        """Convert an MIOpen fdb_key string into a dictionary of its components.
        
        Args:
            fdb_key (str): The fdb_key string to parse, e.g.
            "3-16-16-16-3x3x3-8-14-14-14-100-0x0x0-1x1x1-1x1x1-0-NCDHW-FP32-F".
            
        Returns:
            Dict[str, Any]: A dictionary with keys like 'spatial_dim', 'input_size', 'output_size',
            'filter_size', 'padding_size', 'stride_size', 'dilation_size', 'batch_size',
            'bias', 'layout', 'precision', 'direction', 'group_size' and their corresponding values

        Note: function lifted from MIOpenFF repo
        """
        t = fdb_key.split('-')
        spatial_dim = MIOpenConvolution.get_spatial_dim(t)
        
        # process tokens (t)
        if spatial_dim == 2:
            _direction_and_optionals = t[-1].split('_')
            direction = _direction_and_optionals[0]
            _optionals = _direction_and_optionals[1:]
            
            if direction == "F":
                input_size = f"{t[0]}x1x{t[1]}x{t[2]}"
                output_size = f"{t[4]}x1x{t[5]}x{t[6]}"
            else:
                input_size = f"{t[4]}x1x{t[5]}x{t[6]}"
                output_size = f"{t[0]}x1x{t[1]}x{t[2]}"
            
            filter_size = f"1x{t[3]}"
            batch_size = t[7]
            padding_size = f"0x{t[8]}"
            stride_size = f"1x{t[9]}"
            dilation_size = f"1x{t[10]}"
            bias = t[11]
            
            if len(t) == 15:
                layout = t[12]
            elif len(t) == 17:
                if t[12] == t[13] and t[13] == t[14]:
                    layout = t[12]
                else:
                    layout = f"in {t[12]} fil {t[13]} out {t[14]}"
            else:
                raise ValueError(f"Unable to parse fdb_key: {fdb_key}")
            
            precision = t[-2]
        
        elif spatial_dim == 3:
            _direction_and_optionals = t[-1].split('_')
            direction = _direction_and_optionals[0]
            _optionals = _direction_and_optionals[1:]
            
            if direction == "F":
                input_size = "x".join(t[0:4])
                output_size = "x".join(t[5:9])
            else:
                input_size = "x".join(t[5:9])
                output_size = "x".join(t[0:4])
            
            filter_size = t[4]
            batch_size = t[9]
            padding_size = t[10]
            stride_size = t[11]
            dilation_size = t[12]
            bias = t[13]
            
            if len(t) == 17:
                layout = t[14]
            elif len(t) == 19:
                if t[14] == t[15] and t[15] == t[16]:
                    layout = t[14]
                else:
                    layout = f"in {t[14]} fil {t[15]} out {t[16]}"
            else:
                raise ValueError(f"Unable to parse fdb_key: {fdb_key}")
            
            precision = t[-2]
        
        else:
            raise ValueError(f"Unable to parse fdb_key: {fdb_key}")
        
        # process optionals
        if len(_optionals) == 0:
            group_size = "1"
        elif len(_optionals) == 1:
            # => there is an optional value, and it's the group size
            if _optionals[0].startswith('g'):
                group_size = _optionals[0][1:]
            else:
                raise ValueError(f"Unknown optional in fdb_key: {_optionals[0]}")
        else:
            raise ValueError("fdb_key contains unsupported optionals.\n"
                           "only group size is supported at the moment.")
        
        keys = [
            "spatial_dim",
            "input_size",
            "output_size",
            "filter_size",
            "padding_size",
            "stride_size",
            "dilation_size",
            "batch_size",
            "bias",
            "layout",
            "precision",
            "direction",
            "group_size",
        ]
        
        values = [spatial_dim, input_size, output_size, filter_size, padding_size,
                  stride_size, dilation_size, batch_size, bias, layout,
                  precision, direction, group_size]
        
        return dict(zip(keys, values))
    
    @property
    def spatial_dim(self) -> int:
        """Return spatial dimension"""
        return 3 if self.in_d is not None else 2
    
    @property
    def default_layout(self) -> str:
        """Return layout based on spatial dimension"""
        return 'NCDHW' if self.spatial_dim == 3 else 'NCHW'
    
    def normalize_layouts(self):
        """Fill in layout defaults"""
        default = self.default_layout
        if self.in_layout is None:
            self.in_layout = default
        if self.fil_layout is None:
            self.fil_layout = default
        if self.out_layout is None:
            self.out_layout = default
    
    def to_normalized_tuple(self) -> tuple:
        """
        Normalize for comparison.
        
        Note: Bias field is excluded, doesn't seem to be in commands
        """
        self.normalize_layouts()
        
        if self.spatial_dim == 3:
            return (
                self.batchsize, self.in_channels, self.in_d, self.in_h, self.in_w,
                self.out_channels, self.fil_d, self.fil_h, self.fil_w,
                self.pad_d, self.pad_h, self.pad_w,
                self.conv_stride_d, self.conv_stride_h, self.conv_stride_w,
                self.dilation_d, self.dilation_h, self.dilation_w,
                self.group_count,
                self.in_layout, self.fil_layout, self.out_layout,
                self.precision, self.direction
            )
        else:
            return (
                self.batchsize, self.in_channels, self.in_h, self.in_w,
                self.out_channels, self.fil_h, self.fil_w,
                self.pad_h, self.pad_w,
                self.conv_stride_h, self.conv_stride_w,
                self.dilation_h, self.dilation_w,
                self.group_count,
                self.in_layout, self.fil_layout, self.out_layout,
                self.precision, self.direction
            )
    
    def __eq__(self, other) -> bool:
        """Check if normalized entries are equal"""
        if not isinstance(other, MIOpenConvolution):
            return False
        return self.to_normalized_tuple() == other.to_normalized_tuple()
    
    def __hash__(self) -> int:
        """Allow using MIOpenConvolution in sets and as dict keys"""
        return hash(self.to_normalized_tuple())
    
    @staticmethod
    def _parse_layout(layout_str: str) -> tuple:
        """Parse layout string into tuple"""
        if layout_str.startswith('in '):
            parts = layout_str.split()
            return parts[1], parts[3], parts[5]
        return layout_str, layout_str, layout_str
    
    @classmethod
    def _from_parsed_dict(cls, parsed: Dict[str, Any]) -> 'MIOpenConvolution':
        """Create MIOpenConvolution from a dict"""

        spatial_dim = parsed['spatial_dim']
        input_parts = parsed['input_size'].split('x')
        output_parts = parsed['output_size'].split('x')
        filter_parts = parsed['filter_size'].split('x')
        pad_parts = parsed['padding_size'].split('x')
        stride_parts = parsed['stride_size'].split('x')
        dilation_parts = parsed['dilation_size'].split('x')
        
        in_layout, fil_layout, out_layout = cls._parse_layout(parsed['layout'])
        
        kwargs = {
            'batchsize': int(parsed['batch_size']),
            'in_channels': int(input_parts[0]),
            'out_channels': int(output_parts[0]),
            'group_count': int(parsed['group_size']),
            'bias': int(parsed['bias']),
            'precision': parsed['precision'],
            'direction': parsed['direction'],
            'in_layout': in_layout,
            'fil_layout': fil_layout,
            'out_layout': out_layout,
        }
        
        if spatial_dim == 3:
            kwargs.update({
                'in_d': int(input_parts[1]),
                'in_h': int(input_parts[2]),
                'in_w': int(input_parts[3]),
                'fil_d': int(filter_parts[0]),
                'fil_h': int(filter_parts[1]),
                'fil_w': int(filter_parts[2]),
                'pad_d': int(pad_parts[0]),
                'pad_h': int(pad_parts[1]),
                'pad_w': int(pad_parts[2]),
                'conv_stride_d': int(stride_parts[0]),
                'conv_stride_h': int(stride_parts[1]),
                'conv_stride_w': int(stride_parts[2]),
                'dilation_d': int(dilation_parts[0]),
                'dilation_h': int(dilation_parts[1]),
                'dilation_w': int(dilation_parts[2]),
            })
        else:
            kwargs.update({
                'in_h': int(input_parts[2]),
                'in_w': int(input_parts[3]),
                'fil_h': int(filter_parts[1]),
                'fil_w': int(filter_parts[2]),
                'pad_h': int(pad_parts[1]),
                'pad_w': int(pad_parts[2]),
                'conv_stride_h': int(stride_parts[1]),
                'conv_stride_w': int(stride_parts[2]),
                'dilation_h': int(dilation_parts[1]),
                'dilation_w': int(dilation_parts[2]),
            })
        
        return cls(**kwargs)
    
    @classmethod
    def from_db_key(cls, db_key: str) -> 'MIOpenConvolution':
        """Create MIOpenConvolution from DB key
        
        Args:
            db_key: Database key from .ufdb.txt file
            
        Returns:
            MIOpenConvolution instance
        
        Raises:
            ValueError: If the database key has invalid schema or is empty
        """
        if not db_key or not db_key.strip():
            print("Skipping empty database key")
            raise ValueError("Empty database key")
        
        cls.validate_db_key_schema(db_key)
        
        if '=' in db_key:
            db_key = db_key.split('=')[0]
        
        parsed = cls.fdb_key_to_dict(db_key)
        return cls._from_parsed_dict(parsed)
    
    @classmethod
    def from_miopendriver_command(cls, command: str) -> 'MIOpenConvolution':
        """Create MIOpenConvolution from a MIOpenDriver command.
        
        Args:
            command: MIOpenDriver command string
            
        Returns:
            MIOpenConvolution instance
            
        Raises:
            ValueError: If the command is empty
        """
        if not command or not command.strip():
            print("Skipping empty MIOpenDriver command")
            raise ValueError("Empty command")
        
        parts = command.split()
        
        precision: str = 'FP32'
        for part in parts:
            if part.startswith('conv'):
                if 'bfp16' in part or 'bf16' in part:
                    precision = 'BF16'
                elif 'fp16' in part or 'f16' in part:
                    precision = 'FP16'
                elif 'int8' in part:
                    precision = 'INT8'
                break
        
        flag_map = {flag: field for field, flags in cls.MIOPENDRIVER_FLAGS.items() for flag in flags}
        
        params: Dict[str, Any] = {
            'precision': precision
        }

        i = 0
        while i < len(parts):
            if parts[i].startswith('-') and i + 1 < len(parts) and parts[i] in flag_map:
                params[flag_map[parts[i]]] = parts[i + 1]
                i += 2
            else:
                i += 1
        
        if 'direction' in params:
            params['direction'] = {'1': 'F', '2': 'B', '4': 'W'}.get(params['direction'], params['direction'])
        
        spatial_dim = 3 if 'in_d' in params else 2
        layout_map = {'0': 'NCDHW', '2': 'NDHWC'} if spatial_dim == 3 else {'0': 'NCHW', '1': 'NHWC'}
        
        for layout_key in ['in_layout', 'fil_layout', 'out_layout']:
            if layout_key in params:
                params[layout_key] = layout_map.get(params[layout_key], params[layout_key])
        
        kwargs: Dict[str, Any] = {
            'precision': precision,
            'batchsize': int(params.get('batchsize', 1)),
            'in_channels': int(params.get('in_channels', 1)),
            'in_h': int(params.get('in_h', 32)),
            'in_w': int(params.get('in_w', 32)),
            'out_channels': int(params.get('out_channels', 1)),
            'fil_h': int(params.get('fil_h', 3)),
            'fil_w': int(params.get('fil_w', 3)),
            'pad_h': int(params.get('pad_h', 0)),
            'pad_w': int(params.get('pad_w', 0)),
            'conv_stride_h': int(params.get('conv_stride_h', 1)),
            'conv_stride_w': int(params.get('conv_stride_w', 1)),
            'dilation_h': int(params.get('dilation_h', 1)),
            'dilation_w': int(params.get('dilation_w', 1)),
            'group_count': int(params.get('group_count', 1)),
            'bias': int(params.get('bias', 0)),
            'direction': params.get('direction', 'F'),
        }
        
        if spatial_dim == 3:
            kwargs.update({
                'in_d': int(params.get('in_d', 32)),
                'fil_d': int(params.get('fil_d', 3)),
                'pad_d': int(params.get('pad_d', 0)),
                'conv_stride_d': int(params.get('conv_stride_d', 1)),
                'dilation_d': int(params.get('dilation_d', 1)),
            })

        for layout_key in ['in_layout', 'fil_layout', 'out_layout']:
            if layout_key in params:
                kwargs[layout_key] = params[layout_key]
        
        return cls(**kwargs)
