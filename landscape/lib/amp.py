"""Expose the methods of a remote object over AMP. """

from twisted.internet.protocol import ServerFactory
from twisted.protocols.amp import Argument, String, Command, AMP

from landscape.lib.bpickle import loads, dumps, dumps_table


class Method(object):
    """Marker to expose an object's method in a L{MethodCallProtocol}.

    This class is used when sub-classing a L{MethodCallProtocol} for declaring
    the object's methods call the protocol will respond to.
    """

    def __init__(self, name, **kwargs):
        """
        @param name: The name of a callable method.
        @param kwargs: Optional additional protocol-specific keyword
            argument that must be passed to the method when call it.  Their
            default value  will be treated as a protocol attribute name
            to be passed to the object method as extra argument.  It useful
            when the remote object method we want to call needs to be passed
            some extra protocol-specific argument that the connected client
            can't know (for example the protocol object itself).
        """
        self.name = name
        self.kwargs = kwargs


class MethodCallArgument(Argument):
    """A bpickle-compatbile argument."""

    def toString(self, inObject):
        """Serialize an argument."""
        return dumps(inObject)

    def fromString(self, inString):
        """Unserialize an argument."""
        return loads(inString)

    @classmethod
    def check(cls, inObject):
        """Check if an argument is serializable."""
        return type(inObject) in dumps_table


class MethodCallError(Exception):
    """Raised when a L{MethodCall} command fails."""


class MethodCall(Command):
    """Call a method on the object exposed by a L{MethodCallProtocol}."""

    arguments = [("name", String()),
                 ("args", MethodCallArgument(optional=True)),
                 ("kwargs", MethodCallArgument(optional=True))]

    response = [("result", MethodCallArgument())]

    errors = {MethodCallError: "METHOD_CALL_ERROR"}


class RemoteObject(object):
    """An object able to transparently call methods on a remote object.

    @ivar protocol: A reference to a connected L{MethodCallProtocol}, which
        will be used to send L{MethodCall} commands.
    """

    def __init__(self, protocol):
        self._protocol = protocol

    @property
    def protocol(self):
        """Return a reference to the connected L{MethodCallProtocol}."""
        return self._protocol

    def __getattr__(self, name):
        return self._create_method_call_sender(name)

    def _create_method_call_sender(self, name):
        """Create a L{MethodCall} sender for the method with the given C{name}.

        When the created function is called, it sends the an appropriate
        L{MethodCall} to the remote peer passing it the arguments and
        keyword arguments it was called with, and returing a L{Deferred}
        resulting in the L{MethodCall}'s response value.

        The generated L{MethodCall} will invoke the remote object method
        named C{name}.
        """

        def send_method_call(*args, **kwargs):
            method_call_name = name
            method_call_args = args[:]
            method_call_kwargs = kwargs.copy()
            called = self.protocol.callRemote(MethodCall,
                                              name=method_call_name,
                                              args=method_call_args,
                                              kwargs=method_call_kwargs)
            return called.addCallback(lambda response: response["result"])

        return send_method_call


class MethodCallProtocol(AMP):
    """Expose methods of a local object and call methods on a remote one.

    The object to be exposed (if any) is expected to be the C{object} attribute
    of the factory the protocol was created by.

    @cvar methods: A list of L{Method}s describing the methods that can be
        called with the protocol. It must be defined by sub-classes.
    @cvar remote_factory: The factory used to build the C{remote} attribute.
    @ivar remote: A L{RemoteObject} able to transparently call methods on
        to the actuall object exposed by the remote peer protocol.
    """

    methods = []
    remote_factory = RemoteObject

    def __init__(self):
        """Create the L{RemoteObject} and initialize our internal state."""
        super(MethodCallProtocol, self).__init__()
        self.remote = self.remote_factory(self)
        self._methods_by_name = {}
        for method in self.methods:
            self._methods_by_name[method.name] = method

    @MethodCall.responder
    def _call_object_method(self, name, args, kwargs):
        """Call an object's method with the given arguments.

        If a connected client sends a L{MethodCall} with name C{foo_bar}, then
        the actual method C{foo_bar} of the object associated with the protocol
        will be called with the given C{args} and C{kwargs} and its return
        value delivered back to the client as response to the command.

        The L{MethodCall}'s C{args} and C{kwargs} arguments  will be passed to
        the actual method when calling it.
        """
        method = self._methods_by_name.get(name, None)
        if method is None:
            raise MethodCallError("Forbidden method '%s'" % name)

        method_func = getattr(self.factory.object, name)
        method_args = []
        method_kwargs = {}

        if args:
            method_args.extend(args)
        if kwargs:
            method_kwargs.update(kwargs)
        if method.kwargs:
            for key, value in method.kwargs.iteritems():
                method_kwargs[key] = get_nested_attr(self, value)

        result = method_func(*method_args, **method_kwargs)
        if not MethodCallArgument.check(result):
            raise MethodCallError("Non-serializable result")
        return {"result": result}


class MethodCallServerFactory(ServerFactory):
    """Factory for building L{MethodCallProtocol}s.

    @ivar object: The object exposed by the protocol instances that we build,
        it can be passed to the constructor or set later directly.
    """

    protocol = MethodCallProtocol

    def __init__(self, object):
        """
        @param object: The object exposed by the L{MethodCallProtocol}s
            instances created by this factory.
        """
        self.object = object


def get_nested_attr(obj, path):
    """Like C{getattr} but works with nested attributes as well.

    @param obj: The object we want to get the attribute of.
    @param path: The path to the attribute, like C{.some.nested.attr},
        if C{.} is given the object itself is returned.
    """
    attr = obj
    if path != "":
        for name in path.split(".")[:]:
            attr = getattr(attr, name)
    return attr
