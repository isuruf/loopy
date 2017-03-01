from __future__ import division

__copyright__ = "Copyright (C) 2012 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


from pymbolic import var
import numpy as np

from loopy.symbolic import FunctionIdentifier
from loopy.diagnostic import LoopyError
from loopy.types import NumpyType


class ReductionOperation(object):
    """Subclasses of this type have to be hashable, picklable, and
    equality-comparable.
    """

    def result_dtypes(self, target, *arg_dtypes):
        """
        :arg arg_dtypes: may be None if not known
        :returns: None if not known, otherwise the returned type
        """

        raise NotImplementedError

    @property
    def arg_count(self):
        raise NotImplementedError

    def neutral_element(self, *dtypes):
        raise NotImplementedError

    def __hash__(self):
        # Force subclasses to override
        raise NotImplementedError

    def __eq__(self, other):
        # Force subclasses to override
        raise NotImplementedError

    def __call__(self, dtype, operand1, operand2):
        raise NotImplementedError

    def __ne__(self, other):
        return not self.__eq__(other)

    @staticmethod
    def parse_result_type(target, op_type):
        try:
            return NumpyType(np.dtype(op_type))
        except TypeError:
            pass

        if op_type.startswith("vec_"):
            try:
                return NumpyType(target.get_or_register_dtype(op_type[4:]))
            except AttributeError:
                pass

        raise LoopyError("unable to parse reduction type: '%s'"
                % op_type)


class ScalarReductionOperation(ReductionOperation):
    def __init__(self, forced_result_type=None):
        """
        :arg forced_result_type: Force the reduction result to be of this type.
            May be a string identifying the type for the backend under
            consideration.
        """
        self.forced_result_type = forced_result_type

    @property
    def arg_count(self):
        return 1

    def result_dtypes(self, kernel, arg_dtype):
        if self.forced_result_type is not None:
            return (self.parse_result_type(
                    kernel.target, self.forced_result_type),)

        if arg_dtype is None:
            return None

        return (arg_dtype,)

    def __hash__(self):
        return hash((type(self), self.forced_result_type))

    def __eq__(self, other):
        return (type(self) == type(other)
                and self.forced_result_type == other.forced_result_type)

    def __str__(self):
        result = type(self).__name__.replace("ReductionOperation", "").lower()

        if self.forced_result_type is not None:
            result = "%s<%s>" % (result, str(self.forced_result_type))

        return result


class SumReductionOperation(ScalarReductionOperation):
    def neutral_element(self, dtype):
        return 0

    def __call__(self, dtype, operand1, operand2):
        return operand1 + operand2


class ProductReductionOperation(ScalarReductionOperation):
    def neutral_element(self, dtype):
        return 1

    def __call__(self, dtype, operand1, operand2):
        return operand1 * operand2


def get_le_neutral(dtype):
    """Return a number y that satisfies (x <= y) for all y."""

    if dtype.numpy_dtype.kind == "f":
        # OpenCL 1.1, section 6.11.2
        return var("INFINITY")
    elif dtype.numpy_dtype.kind == "i":
        # OpenCL 1.1, section 6.11.3
        if dtype.numpy_dtype.itemsize == 4:
            #32 bit integer
            return var("INT_MAX")
        elif dtype.numpy_dtype.itemsize == 8:
            #64 bit integer
            return var('LONG_MAX')
    else:
        raise NotImplementedError("less")


def get_ge_neutral(dtype):
    """Return a number y that satisfies (x >= y) for all y."""

    if dtype.numpy_dtype.kind == "f":
        # OpenCL 1.1, section 6.11.2
        return -var("INFINITY")
    elif dtype.numpy_dtype.kind == "i":
        # OpenCL 1.1, section 6.11.3
        if dtype.numpy_dtype.itemsize == 4:
            #32 bit integer
            return var("INT_MIN")
        elif dtype.numpy_dtype.itemsize == 8:
            #64 bit integer
            return var('LONG_MIN')
    else:
        raise NotImplementedError("less")


class MaxReductionOperation(ScalarReductionOperation):
    def neutral_element(self, dtype):
        return get_ge_neutral(dtype)

    def __call__(self, dtype, operand1, operand2):
        return var("max")(operand1, operand2)


class MinReductionOperation(ScalarReductionOperation):
    def neutral_element(self, dtype):
        return get_le_neutral(dtype)

    def __call__(self, dtype, operand1, operand2):
        return var("min")(operand1, operand2)


# {{{ segmented reduction

class _SegmentedScalarReductionOperation(ReductionOperation):
    def __init__(self, **kwargs):
        self.inner_reduction = self.base_reduction_class(**kwargs)

    @property
    def arg_count(self):
        return 2

    def prefix(self, scalar_dtype, segment_flag_dtype):
        return "loopy_segmented_%s_%s_%s" % (self.which,
                scalar_dtype.numpy_dtype.type.__name__,
                segment_flag_dtype.numpy_dtype.type.__name__)

    def neutral_element(self, scalar_dtype, segment_flag_dtype):
        return SegmentedFunction(self, (scalar_dtype, segment_flag_dtype), "init")()

    def result_dtypes(self, kernel, scalar_dtype, segment_flag_dtype):
        return (self.inner_reduction.result_dtypes(kernel, scalar_dtype)
                + (segment_flag_dtype,))

    def __str__(self):
        return "segmented_" + self.which

    def __hash__(self):
        return hash(type(self))

    def __eq__(self, other):
        return type(self) == type(other)

    def __call__(self, dtypes, operand1, operand2):
        return SegmentedFunction(self, dtypes, "update")(*(operand1 + operand2))


class SegmentedSumReductionOperation(_SegmentedScalarReductionOperation):
    base_reduction_class = SumReductionOperation
    which = "sum"
    op = "((%s) + (%s))"


class SegmentedProductReductionOperation(_SegmentedScalarReductionOperation):
    base_reduction_class = ProductReductionOperation
    op = "((%s) * (%s))"
    which = "product"


class SegmentedFunction(FunctionIdentifier):
    init_arg_names = ("reduction_op", "dtypes", "name")

    def __init__(self, reduction_op, dtypes, name):
        """
        :arg dtypes: A :class:`tuple` of `(scalar_dtype, segment_flag_dtype)`
        """
        self.reduction_op = reduction_op
        self.dtypes = dtypes
        self.name = name

    @property
    def scalar_dtype(self):
        return self.dtypes[0]

    @property
    def segment_flag_dtype(self):
        return self.dtypes[1]

    def __getinitargs__(self):
        return (self.reduction_op, self.dtypes, self.name)


def get_segmented_function_preamble(kernel, func_id):
    op = func_id.reduction_op
    prefix = op.prefix(func_id.scalar_dtype, func_id.segment_flag_dtype)

    from pymbolic.mapper.c_code import CCodeMapper

    c_code_mapper = CCodeMapper()

    return (prefix, """
    inline %(scalar_t)s %(prefix)s_init(%(segment_flag_t)s *segment_flag_out)
    {
        *segment_flag_out = 0;
        return %(neutral)s;
    }

    inline %(scalar_t)s %(prefix)s_update(
        %(scalar_t)s op1, %(segment_flag_t)s segment_flag1,
        %(scalar_t)s op2, %(segment_flag_t)s segment_flag2,
        %(segment_flag_t)s *segment_flag_out)
    {
        *segment_flag_out = segment_flag2;
        return segment_flag2 ? op2 : %(combined)s;
    }
    """ % dict(
            scalar_t=kernel.target.dtype_to_typename(func_id.scalar_dtype),
            prefix=prefix,
            segment_flag_t=kernel.target.dtype_to_typename(
                    func_id.segment_flag_dtype),
            neutral=c_code_mapper(
                    op.inner_reduction.neutral_element(func_id.scalar_dtype)),
            combined=op.op % ("op1", "op2"),
            ))


# }}}


# {{{ argmin/argmax

class _ArgExtremumReductionOperation(ReductionOperation):
    def prefix(self, scalar_dtype, index_dtype):
        return "loopy_arg%s_%s_%s" % (self.which,
                index_dtype.numpy_dtype.type.__name__,
                scalar_dtype.numpy_dtype.type.__name__)

    def result_dtypes(self, kernel, scalar_dtype, index_dtype):
        return (scalar_dtype, index_dtype)

    def neutral_element(self, scalar_dtype, index_dtype):
        return ArgExtFunction(self, (scalar_dtype, index_dtype), "init")()

    def __str__(self):
        return self.which

    def __hash__(self):
        return hash(type(self))

    def __eq__(self, other):
        return type(self) == type(other)

    @property
    def arg_count(self):
        return 2

    def __call__(self, dtypes, operand1, operand2):
        return ArgExtFunction(self, dtypes, "update")(*(operand1 + operand2))


class ArgMaxReductionOperation(_ArgExtremumReductionOperation):
    which = "max"
    update_comparison = ">="
    neutral_sign = -1


class ArgMinReductionOperation(_ArgExtremumReductionOperation):
    which = "min"
    update_comparison = "<="
    neutral_sign = +1


class ArgExtFunction(FunctionIdentifier):
    init_arg_names = ("reduction_op", "dtypes", "name")

    def __init__(self, reduction_op, dtypes, name):
        self.reduction_op = reduction_op
        self.dtypes = dtypes
        self.name = name

    @property
    def scalar_dtype(self):
        return self.dtypes[0]

    @property
    def index_dtype(self):
        return self.dtypes[1]

    def __getinitargs__(self):
        return (self.reduction_op, self.dtypes, self.name)


def get_argext_preamble(kernel, func_id):
    op = func_id.reduction_op
    prefix = op.prefix(func_id.scalar_dtype, func_id.index_dtype)

    from pymbolic.mapper.c_code import CCodeMapper

    c_code_mapper = CCodeMapper()

    neutral = get_ge_neutral if op.neutral_sign < 0 else get_le_neutral

    return (prefix, """
    inline %(scalar_t)s %(prefix)s_init(%(index_t)s *index_out)
    {
        *index_out = INT_MIN;
        return %(neutral)s;
    }

    inline %(scalar_t)s %(prefix)s_update(
        %(scalar_t)s op1, %(index_t)s index1,
        %(scalar_t)s op2, %(index_t)s index2,
        %(index_t)s *index_out)
    {
        if (op2 %(comp)s op1)
        {
            *index_out = index2;
            return op2;
        }
        else
        {
            *index_out = index1;
            return op1;
        }
    }
    """ % dict(
            scalar_t=kernel.target.dtype_to_typename(func_id.scalar_dtype),
            prefix=prefix,
            index_t=kernel.target.dtype_to_typename(func_id.index_dtype),
            neutral=c_code_mapper(neutral(func_id.scalar_dtype)),
            comp=op.update_comparison,
            ))

# }}}


# {{{ reduction op registry

_REDUCTION_OPS = {
        "sum": SumReductionOperation,
        "product": ProductReductionOperation,
        "max": MaxReductionOperation,
        "min": MinReductionOperation,
        "argmax": ArgMaxReductionOperation,
        "argmin": ArgMinReductionOperation,
        "segmented_sum": SegmentedSumReductionOperation,
        "segmented_product": SegmentedProductReductionOperation,
        }

_REDUCTION_OP_PARSERS = [
        ]


def register_reduction_parser(parser):
    """Register a new :class:`ReductionOperation`.

    :arg parser: A function that receives a string and returns
        a subclass of ReductionOperation.
    """
    _REDUCTION_OP_PARSERS.append(parser)


def parse_reduction_op(name):
    import re

    red_op_match = re.match(r"^([a-z]+)_([a-z0-9_]+)$", name)
    if red_op_match:
        op_name = red_op_match.group(1)
        op_type = red_op_match.group(2)

        if op_name in _REDUCTION_OPS:
            return _REDUCTION_OPS[op_name](op_type)

    if name in _REDUCTION_OPS:
        return _REDUCTION_OPS[name]()

    for parser in _REDUCTION_OP_PARSERS:
        result = parser(name)
        if result is not None:
            return result

    return None

# }}}


def reduction_function_mangler(kernel, func_id, arg_dtypes):
    if isinstance(func_id, ArgExtFunction) and func_id.name == "init":
        from loopy.target.opencl import OpenCLTarget
        if not isinstance(kernel.target, OpenCLTarget):
            raise LoopyError("only OpenCL supported for now")

        op = func_id.reduction_op

        from loopy.kernel.data import CallMangleInfo
        return CallMangleInfo(
                target_name="%s_init" % op.prefix(
                    func_id.scalar_dtype, func_id.index_dtype),
                result_dtypes=op.result_dtypes(
                    kernel, func_id.scalar_dtype, func_id.index_dtype),
                arg_dtypes=(),
                )

    elif isinstance(func_id, ArgExtFunction) and func_id.name == "update":
        from loopy.target.opencl import OpenCLTarget
        if not isinstance(kernel.target, OpenCLTarget):
            raise LoopyError("only OpenCL supported for now")

        op = func_id.reduction_op

        from loopy.kernel.data import CallMangleInfo
        return CallMangleInfo(
                target_name="%s_update" % op.prefix(
                    func_id.scalar_dtype, func_id.index_dtype),
                result_dtypes=op.result_dtypes(
                    kernel, func_id.scalar_dtype, func_id.index_dtype),
                arg_dtypes=(
                    func_id.scalar_dtype,
                    kernel.index_dtype,
                    func_id.scalar_dtype,
                    kernel.index_dtype),
                )

    elif isinstance(func_id, SegmentedFunction) and func_id.name == "init":
        from loopy.target.opencl import OpenCLTarget
        if not isinstance(kernel.target, OpenCLTarget):
            raise LoopyError("only OpenCL supported for now")

        op = func_id.reduction_op

        from loopy.kernel.data import CallMangleInfo
        return CallMangleInfo(
                target_name="%s_init" % op.prefix(
                    func_id.scalar_dtype, func_id.segment_flag_dtype),
                result_dtypes=op.result_dtypes(
                    kernel, func_id.scalar_dtype, func_id.segment_flag_dtype),
                arg_dtypes=(),
                )

    elif isinstance(func_id, SegmentedFunction) and func_id.name == "update":
        from loopy.target.opencl import OpenCLTarget
        if not isinstance(kernel.target, OpenCLTarget):
            raise LoopyError("only OpenCL supported for now")

        op = func_id.reduction_op

        from loopy.kernel.data import CallMangleInfo
        return CallMangleInfo(
                target_name="%s_update" % op.prefix(
                    func_id.scalar_dtype, func_id.segment_flag_dtype),
                result_dtypes=op.result_dtypes(
                    kernel, func_id.scalar_dtype, func_id.segment_flag_dtype),
                arg_dtypes=(
                    func_id.scalar_dtype,
                    func_id.segment_flag_dtype,
                    func_id.scalar_dtype,
                    func_id.segment_flag_dtype),
                )

    return None


def reduction_preamble_generator(preamble_info):
    from loopy.target.opencl import OpenCLTarget

    for func in preamble_info.seen_functions:
        if isinstance(func.name, ArgExtFunction):
            if not isinstance(preamble_info.kernel.target, OpenCLTarget):
                raise LoopyError("only OpenCL supported for now")

            yield get_argext_preamble(preamble_info.kernel, func.name)

        elif isinstance(func.name, SegmentedFunction):
            if not isinstance(preamble_info.kernel.target, OpenCLTarget):
                raise LoopyError("only OpenCL supported for now")

            yield get_segmented_function_preamble(preamble_info.kernel, func.name)

# vim: fdm=marker
