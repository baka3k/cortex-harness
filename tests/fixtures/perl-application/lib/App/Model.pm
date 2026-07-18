package App::Model;

use strict;
use warnings;
use App::Util;
no feature 'indirect';
require App::Util;

our $PACKAGE_VALUE = 1;
my $lexical_value = 2;

sub mutable : lvalue {
    $PACKAGE_VALUE;
}

sub run {
    local $PACKAGE_VALUE = 3;
    helper();
    App::Util::helper();

    my $object = bless {}, 'App::Model';
    $object->execute();

    my $callback = \&App::Util::helper;
    $callback->();
    eval 'App::Util::helper()';
    return $lexical_value;
}

sub execute {
    return App::Util::helper();
}

package App::Secondary;

sub secondary {
    return App::Util::helper();
}

1;
