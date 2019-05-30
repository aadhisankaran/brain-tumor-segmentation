import tensorflow as tf

from model.resnet_block import ConvBlock
from utils.constants import *


class DecoderBlock(tf.keras.layers.Layer):
    def __init__(self,
                 filters,
                 kernel_size=3,
                 data_format='channels_last',
                 groups=8,
                 reduction=4,
                 kernel_regularizer=None,
                 kernel_initializer=tf.initializers.he_normal,
                 use_se=False):
        """Initializes one level of upsampling in the convolutional decoder.

            Each decoder block consists of a pointwise 3D-convolution followed
            by a 3D-upsampling. The output of these 2 layers is summed with
            the corresponding channel-depth output from the encoder and fed
            into a convolutional block.

            Args:
                filters: int
                    The number of filters to use in the 3D convolutional
                    block. The output layer of this green block will have
                    this many number of channels.
                kernel_size: int, optional
                    The size of all convolutional kernels. Defaults to 3,
                    as used in the paper.
                data_format: str, optional
                    The format of the input data. Must be either 'channels_last'
                    or 'channels_first'. Defaults to `channels_last` for CPU
                    development. 'channels_first is used in the paper.
                groups: int, optional
                    The size of each group for GroupNormalization. Defaults to
                    8, as used in the paper.
                reduction: int, optional
                    Reduction ratio for excitation size in squeeze-excitation layer.
                kernel_regularizer: tf.keras.regularizer callable, optional
                    Kernel regularizer for convolutional operations.
        """
        super(DecoderBlock, self).__init__()
        # Set up config for self.get_config() to serialize later.
        self.config = super(DecoderBlock, self).get_config()
        self.config.update({'filters': filters,
                            'kernel_size': kernel_size,
                            'data_format': data_format,
                            'groups': groups,
                            'reduction': reduction,
                            'kernel_regularizer': tf.keras.regularizers.serialize(kernel_regularizer),
                            'use_se': use_se})

        self.conv3d_ptwise = tf.keras.layers.Conv3D(
                                filters=filters,
                                kernel_size=1,
                                strides=1,
                                padding='same',
                                data_format=data_format,
                                kernel_initializer=kernel_initializer,
                                kernel_regularizer=kernel_regularizer)
        self.upsample = tf.keras.layers.UpSampling3D(
                                size=2,
                                data_format=data_format)
        self.residual = tf.keras.layers.Add()
        self.conv_block = ConvBlock(
                                filters=filters,
                                kernel_size=kernel_size,
                                data_format=data_format,
                                groups=groups,
                                reduction=reduction,
                                kernel_regularizer=kernel_regularizer,
                                kernel_initializer = kernel_initializer,
                                use_se=use_se)

    def call(self, inputs):
        """Returns the forward pass of one DecoderBlock.

            { Conv3D_ptwise -> Upsample3D -> Residual -> ConvBlock }
        """
        inputs, enc_res = inputs
        inputs = self.conv3d_ptwise(inputs)
        inputs = self.upsample(inputs)
        inputs = self.residual([inputs, enc_res])
        inputs = self.conv_block(inputs)
        return inputs

    def get_config(self):
        return self.config


class ConvDecoder(tf.keras.layers.Layer):
    def __init__(self,
                 data_format='channels_last',
                 kernel_size=3,
                 groups=8,
                 reduction=4,
                 kernel_regularizer=None,
                 kernel_initializer=tf.initializers.he_normal,
                 use_se=False):
        """Initializes the model decoder.

            See https://arxiv.org/pdf/1810.11654.pdf for more details.
            The model decoder takes the encoder outputs at each level
            of downsampling and upsamples, connecting each recovered
            level with its corresponding encoder level residually. The
            final logit layer outputs a 3-channel Tensor the same size
            as the original input to the encoder.

            Args:
                data_format: str, optional
                    The format of the input data. Must be either 'channels_last'
                    or 'channels_first'. Defaults to `channels_last` for CPU
                    development. 'channels_first is used in the paper.
                kernel_size: int, optional
                    The size of all convolutional kernels. Defaults to 3,
                    as used in the paper.
                groups: int, optional
                    The size of each group for GroupNormalization. Defaults to
                    8, as used in the paper.
                reduction: int, optional
                    Reduction ratio for excitation size in squeeze-excitation layer.
                kernel_regularizer: tf.keras.regularizer callable, optional
                    Kernel regularizer for convolutional operations.
        """
        super(ConvDecoder, self).__init__()
        # Set up config for self.get_config() to serialize later.
        self.config = super(ConvDecoder, self).get_config()
        self.config.update({'data_format': data_format,
                            'kernel_size': kernel_size,
                            'groups': groups,
                            'reduction': reduction,
                            'kernel_regularizer': tf.keras.regularizers.serialize(kernel_regularizer),
                            'use_se': use_se})

        self.dec_block_2 = DecoderBlock(
                                filters=DEC_CONV_BLOCK2_SIZE,
                                kernel_size=kernel_size,
                                data_format=data_format,
                                groups=groups,
                                reduction=reduction,
                                kernel_regularizer=kernel_regularizer,
                                kernel_initializer=kernel_initializer,
                                use_se=use_se)
        self.dec_block_1 = DecoderBlock(
                                filters=DEC_CONV_BLOCK1_SIZE,
                                kernel_size=kernel_size,
                                data_format=data_format,
                                groups=groups,
                                reduction=reduction,
                                kernel_regularizer=kernel_regularizer,
                                kernel_initializer=kernel_initializer,
                                use_se=use_se)
        self.dec_block_0 = DecoderBlock(
                                filters=DEC_CONV_BLOCK0_SIZE,
                                kernel_size=kernel_size,
                                data_format=data_format,
                                groups=groups,
                                reduction=reduction,
                                kernel_regularizer=kernel_regularizer,
                                kernel_initializer=kernel_initializer,
                                use_se=use_se)

        self.conv_out = tf.keras.layers.Conv3D(
                                filters=DEC_CONV_LAYER_SIZE,
                                kernel_size=kernel_size,
                                strides=1,
                                padding='same',
                                data_format=data_format,
                                kernel_regularizer=kernel_regularizer,
                                kernel_initializer=kernel_initializer)

        self.ptwise_logits = tf.keras.layers.Conv3D(
                                filters=OUT_CH-1,
                                kernel_size=1,
                                strides=1,
                                padding='same',
                                data_format=data_format,
                                kernel_regularizer=kernel_regularizer,
                                kernel_initializer=kernel_initializer,
                                activation='sigmoid')

    def call(self, inputs):
        """Returns the forward pass of the ConvDecoder.

            {
                DecoderBlock_128 -> DecoderBlock_64 -> DecoderBlock_32,
                OutputConv_32 -> LogitConv_3 + Sigmoid
            }

            Args:
                enc_outs: (Tensor, Tensor, Tensor, Tensor)
                    Contains residual outputs of the encoder from forward
                    pass. Must contain (in order) the ConvBlock outputs at
                    the 32, 64, 128, and 256 filter sizes.

            Returns:
                x: Tensor
                    Output 'image' the same size as the original input image
                    to the encoder, but with 3 channels.
        """
        enc_out_0, enc_out_1, enc_out_2, x = inputs

        x = self.dec_block_2((x, enc_out_2))
        x = self.dec_block_1((x, enc_out_1))
        x = self.dec_block_0((x, enc_out_0))

        x = self.conv_out(x)
        x = self.ptwise_logits(x)

        return x

    def get_config(self):
        return self.config
