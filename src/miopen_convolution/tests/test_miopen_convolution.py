import unittest
from parameterized import parameterized # type: ignore[import-untyped]
from miopen_convolution import MIOpenConvolution


class TestMIOpenConvolution(unittest.TestCase):
    """Unified tests for MIOpenConvolution."""

    DB_KEY_TEST_CASES = [
        (
            "128-11-130-194-3x3x3-128-9-128-192-1-0x0x0-1x1x1-1x1x1-0-NCDHW-FP16-F",
            {'spatial_dim': 3, 'batchsize': 1, 'in_channels': 128, 'out_channels': 128,
             'in_d': 11, 'in_h': 130, 'in_w': 194, 'fil_d': 3, 'fil_h': 3, 'fil_w': 3,
             'pad_d': 0, 'pad_h': 0, 'pad_w': 0, 'precision': 'FP16', 'direction': 'F'}
        ),
        (
            "128-11-258-258-3x3x3-3-9-256-256-1-0x0x0-1x1x1-1x1x1-0-NCDHW-FP16-F",
            {'spatial_dim': 3, 'batchsize': 1, 'in_channels': 128, 'out_channels': 3,
             'in_d': 11, 'in_h': 258, 'in_w': 258, 'fil_d': 3, 'fil_h': 3, 'fil_w': 3}
        ),
        (
            "16-33-136-240-1x2x2-3072-33-68-120-1-0x0x0-1x2x2-1x1x1-0-NCDHW-BF16-F",
            {'spatial_dim': 3, 'in_d': 33, 'fil_d': 1, 'precision': 'BF16',
             'conv_stride_d': 1, 'conv_stride_h': 2, 'conv_stride_w': 2}
        ),
        (
            "128-19-146-258-3x3x3-128-17-144-256-1-0x0x0-1x1x1-1x1x1-0-NCDHW-FP16-F",
            {'spatial_dim': 3, 'in_layout': 'NCDHW', 'fil_layout': 'NCDHW', 'out_layout': 'NCDHW'}
        ),
        (
            "128-19-162-194-3x3x3-128-17-160-192-1-0x0x0-1x1x1-1x1x1-0-NCDHW-FP16-F=ConvHipImplicitGemm3DGroupFwdXdlops",
            {'spatial_dim': 3, 'batchsize': 1, 'in_channels': 128}  # Should parse key part before '='
        )
    ]

    COMMAND_TEST_CASES = [
        (
            "MIOpenDriver convbfp16 -n 1 -c 1024 --in_d 1 -H 12 -W 12 -k 1024 --fil_d 1 -y 1 -x 1 --pad_d 0 -p 0 -q 0 --conv_stride_d 1 -u 1 -v 1 --dilation_d 1 -l 1 -j 1 --spatial_dim 3 --in_layout NDHWC --fil_layout NDHWC --out_layout NDHWC -m conv -g 1 -F 1 -t 1",
            {'spatial_dim': 3, 'batchsize': 1, 'in_channels': 1024, 'out_channels': 1024,
             'in_d': 1, 'in_h': 12, 'in_w': 12, 'fil_d': 1, 'fil_h': 1, 'fil_w': 1,
             'precision': 'BF16', 'direction': 'F', 'in_layout': 'NDHWC'}
        ),
        (
            "MIOpenDriver convbfp16 -n 1 -c 1024 --in_d 16 -H 34 -W 60 -k 128 --fil_d 3 -y 3 -x 3 --pad_d 1 -p 1 -q 1 --conv_stride_d 1 -u 1 -v 1 --dilation_d 1 -l 1 -j 1 --spatial_dim 3 -m conv -g 1 -F 1 -t 1",
            {'spatial_dim': 3, 'in_d': 16, 'fil_d': 3, 'pad_d': 1, 'precision': 'BF16',
             'in_channels': 1024, 'out_channels': 128}
        ),
        (
            "MIOpenDriver convbfp16 -n 1 -c 1024 --in_d 3 -H 14 -W 14 -k 64 --fil_d 3 -y 3 -x 3 --pad_d 0 -p 0 -q 0 --conv_stride_d 1 -u 1 -v 1 --dilation_d 1 -l 1 -j 1 --spatial_dim 3 -m conv -g 1 -F 1 -t 1",
            {'precision': 'BF16', 'in_d': 3, 'out_channels': 64}
        )
    ]

    INVALID_DB_KEYS = [
        ("", "Empty key"),
        ("   ", "Whitespace key"),
        ("3-16-16", "Too few tokens"),
        ("128-11-130-194-3x3x3-128-9-128-192-1-0x0x0-1x1x1-1x1x1-0-NCDHW-FP16", "Missing direction field"),
    ]

    INVALID_COMMANDS = [
        ("", "Empty command"),
        ("   ", "Whitespace command"),
    ]

    EQUALITY_TEST_CASES = [
        (
            "128-11-130-194-3x3x3-128-9-128-192-1-0x0x0-1x1x1-1x1x1-0-NCDHW-FP16-F",
            "128-11-130-194-3x3x3-128-9-128-192-1-0x0x0-1x1x1-1x1x1-0-NCDHW-NCDHW-NCDHW-FP16-F",
            True
        ),
        (
            "1024-16-34-60-3x3x3-128-16-34-60-1-1x1x1-1x1x1-1x1x1-0-NCDHW-BF16-F",
            "MIOpenDriver convbfp16 -n 1 -c 1024 --in_d 16 -H 34 -W 60 -k 128 --fil_d 3 -y 3 -x 3 --pad_d 1 -p 1 -q 1 --conv_stride_d 1 -u 1 -v 1 --dilation_d 1 -l 1 -j 1 --spatial_dim 3 -m conv -g 1 -F 1 -t 1",
            True
        ),
        (
            "MIOpenDriver convbfp16 -n 1 -c 1024 --in_d 1 -H 16 -W 12 -k 1024 --fil_d 1 -y 1 -x 1 --pad_d 0 -p 0 -q 0 --conv_stride_d 1 -u 1 -v 1 --dilation_d 1 -l 1 -j 1 --spatial_dim 3 --in_layout NDHWC --fil_layout NDHWC --out_layout NDHWC -m conv -g 1 -F 1 -t 1",
            "192-4-414-314-3x3x3-384-2-412-312-1-0x0x0-1x1x1-1x1x1-0-NCDHW-BF16-F",
            False
        )
    ]

    @parameterized.expand(DB_KEY_TEST_CASES)
    def test_from_db_key(self, db_key, expected_attrs):
        """Test parsing ufdb keys"""
        conv = MIOpenConvolution.from_db_key(db_key)
        for attr, expected_value in expected_attrs.items():
            self.assertEqual(getattr(conv, attr), expected_value, 
                           f"Attribute {attr} mismatch for key: {db_key}")

    @parameterized.expand(COMMAND_TEST_CASES)
    def test_from_miopendriver_command(self, command, expected_attrs):
        """Test parsing MIOpenDriver commands"""
        conv = MIOpenConvolution.from_miopendriver_command(command)
        for attr, expected_value in expected_attrs.items():
            self.assertEqual(getattr(conv, attr), expected_value,
                           f"Attribute {attr} mismatch for command: {command}")

    @parameterized.expand(INVALID_DB_KEYS)
    def test_validate_db_key_invalid(self, invalid_key, _description):
        """Test invalid ufdb keys"""
        print(f"Testing {_description}")
        with self.assertRaises(ValueError):
            MIOpenConvolution.from_db_key(invalid_key)

    @parameterized.expand(INVALID_COMMANDS)
    def test_validate_command_invalid(self, invalid_command, _description):
        """Test invalid MIOpenDriver commands"""
        print(f"Testing {_description}")
        with self.assertRaises(ValueError):
            MIOpenConvolution.from_miopendriver_command(invalid_command)

    @parameterized.expand(EQUALITY_TEST_CASES)
    def test_equality(self, input1, input2, is_equal):
        """Test equality after normalization (ufdb keys and commands)"""
        print(f"Testing equality for inputs: {input1} and {input2}")
        conv1 = (MIOpenConvolution.from_db_key(input1) 
                if not input1.startswith("MIOpenDriver") 
                else MIOpenConvolution.from_miopendriver_command(input1))
        conv2 = (MIOpenConvolution.from_db_key(input2) 
                if not input2.startswith("MIOpenDriver") 
                else MIOpenConvolution.from_miopendriver_command(input2))
        
        if is_equal:
            self.assertEqual(conv1, conv2)
            self.assertEqual(hash(conv1), hash(conv2))
        else:
            self.assertNotEqual(conv1, conv2)


if __name__ == '__main__':
    unittest.main()
